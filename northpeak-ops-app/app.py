"""NorthPeak Store Ops — Build 2 Decision App.

A Databricks App that:
- Visualizes shortfalls ranked by revenue at risk (Layer 1)
- Provides AI-assisted explanation, what-if, and memo drafting (Layer 2)
- Closes the loop with approve → commit writeback (Layer 3)

Connects to Lakebase (dev branch) for both synced read tables and writable action tables.
Routes all LLM calls through a single configurable endpoint module.
"""
import os
import json
import uuid
import time
import threading
from datetime import datetime, timezone
from typing import Optional

import gradio as gr
import psycopg2
import psycopg2.extras

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG — single module for LLM routing (Build 3 swaps this one setting)
# ═══════════════════════════════════════════════════════════════════════════════
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "databricks-gpt-5-6-luna")
LAKEBASE_HOST = os.environ.get("LAKEBASE_HOST", "ep-calm-band-d2zksq6c.database.us-east-1.cloud.databricks.com")
LAKEBASE_DB = os.environ.get("LAKEBASE_DB", "databricks_postgres")
LAKEBASE_PROJECT = os.environ.get("LAKEBASE_PROJECT", "northpeak")
LAKEBASE_BRANCH = os.environ.get("LAKEBASE_BRANCH", "dev")
MAX_LLM_TOKENS = 1024  # bounded by design for Build 3 budget control


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE CONNECTION
# ═══════════════════════════════════════════════════════════════════════════════
def get_db_connection():
    """Get Lakebase Postgres connection using OAuth token from Databricks SDK."""
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    # Autoscaling Lakebase returns .token (not .username/.password)
    cred = w.postgres.generate_database_credential(
        endpoint=f"projects/{LAKEBASE_PROJECT}/branches/{LAKEBASE_BRANCH}/endpoints/primary"
    )
    # Username is the SP client ID or user email depending on runtime
    pg_user = os.environ.get("PGUSER", w.config.client_id or w.current_user.me().user_name)
    conn = psycopg2.connect(
        host=LAKEBASE_HOST,
        database=LAKEBASE_DB,
        user=pg_user,
        password=cred.token,
        port=5432,
        sslmode="require",
    )
    conn.autocommit = True
    return conn


def execute_query(sql, params=None, fetch=True):
    """Execute a query against Lakebase."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if fetch:
                return cur.fetchall()
            return None
    finally:
        conn.close()


def execute_write(sql, params=None):
    """Execute a write query against Lakebase."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            try:
                return cur.fetchall()
            except psycopg2.ProgrammingError:
                return None
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# LLM CLIENT MODULE — single configurable endpoint (Build 3 reroutes here)
# ═══════════════════════════════════════════════════════════════════════════════
def call_llm(system_prompt: str, user_message: str, max_tokens: int = MAX_LLM_TOKENS) -> str:
    """Route all LLM calls through one configurable endpoint."""
    from databricks.sdk import WorkspaceClient
    import requests as _requests

    w = WorkspaceClient()
    host = w.config.host.rstrip("/")

    # Get auth headers from SDK (handles OAuth SP credentials automatically)
    auth_headers = dict(w.config.authenticate())
    headers = {**auth_headers, "Content-Type": "application/json"}

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
    }
    resp = _requests.post(
        f"{host}/serving-endpoints/{LLM_ENDPOINT}/invocations",
        headers=headers, json=payload, timeout=90
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ═══════════════════════════════════════════════════════════════════════════════
# WORKFLOW STATE — observability table
# ═══════════════════════════════════════════════════════════════════════════════
def record_event(event_type: str, entity_type: str = None, entity_id: str = None,
                 trigger_source: str = None, details: dict = None):
    """Record a workflow/trigger event in the state table."""
    execute_write(
        """INSERT INTO northpeak_app.workflow_state
           (event_type, entity_type, entity_id, trigger_source, details)
           VALUES (%s, %s, %s, %s, %s)""",
        (event_type, entity_type, entity_id, trigger_source,
         json.dumps(details or {}))
    )


def log_assist(session_id: str, request_type: str, request_text: str,
               response_text: str, model_name: str = LLM_ENDPOINT,
               tokens_used: int = None, latency_ms: int = None,
               context_refs: list = None):
    """Log an assistant interaction."""
    execute_write(
        """INSERT INTO northpeak_app.assist_log
           (session_id, request_type, request_text, response_text,
            model_name, tokens_used, latency_ms, context_refs)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (session_id, request_type, request_text, response_text,
         model_name, tokens_used, latency_ms,
         json.dumps(context_refs or []))
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — VISUALIZE: Live shortfall view
# ═══════════════════════════════════════════════════════════════════════════════
VIEW_QUERY = """
WITH latest_snap AS (
    SELECT MAX(snapshot_date) AS max_date
    FROM northpeak.synced_inventory
),
velocity AS (
    SELECT store_id, product_id,
           AVG(on_hand_units) AS avg_daily_drawdown,
           COUNT(*) AS days_tracked
    FROM northpeak.synced_inventory
    WHERE snapshot_date >= (SELECT max_date - INTERVAL '7 days' FROM latest_snap)
    GROUP BY store_id, product_id
),
shortfalls AS (
    SELECT
        i.store_id, i.store_name, i.city, i.climate_zone, i.region,
        i.product_id, i.product_name, i.category,
        i.on_hand_units, i.on_order_units, i.price_usd,
        i.store_lat, i.store_lng,
        COALESCE(v.avg_daily_drawdown, 0) AS avg_velocity,
        CASE WHEN i.on_hand_units = 0 THEN i.price_usd * COALESCE(v.avg_daily_drawdown, 5) * 14
             ELSE i.price_usd * GREATEST(COALESCE(v.avg_daily_drawdown, 5) * 7 - i.on_hand_units, 0)
        END AS lost_sales_exposure_usd,
        CASE WHEN COALESCE(v.avg_daily_drawdown, 1) > 0
             THEN i.on_hand_units / GREATEST(v.avg_daily_drawdown, 0.1)
             ELSE 999 END AS days_of_cover,
        CASE WHEN i.on_hand_units = 0 THEN 'stockout'
             WHEN i.on_hand_units < COALESCE(v.avg_daily_drawdown, 5) * 3 THEN 'at_risk'
             ELSE 'healthy' END AS position_status
    FROM northpeak.synced_inventory i
    CROSS JOIN latest_snap ls
    LEFT JOIN velocity v ON v.store_id = i.store_id AND v.product_id = i.product_id
    WHERE i.snapshot_date = ls.max_date
      AND i.climate_zone = 'North'
      AND i.seasonality = 'cold_weather'
),
recovery_status AS (
    SELECT destination_store_id AS store_id, product_id,
           status AS recovery_status,
           MAX(created_at) AS last_action_at
    FROM northpeak_app.transfer_actions
    GROUP BY destination_store_id, product_id, status
)
SELECT
    s.store_id, s.store_name, s.city, s.region,
    s.product_id, s.product_name,
    s.on_hand_units, s.on_order_units,
    ROUND(s.lost_sales_exposure_usd::numeric, 0) AS lost_sales_exposure_usd,
    ROUND(s.days_of_cover::numeric, 1) AS days_of_cover,
    s.position_status,
    COALESCE(r.recovery_status, 'none') AS recovery_status
FROM shortfalls s
LEFT JOIN recovery_status r ON r.store_id = s.store_id AND r.product_id = s.product_id
WHERE s.position_status IN ('stockout', 'at_risk')
ORDER BY s.lost_sales_exposure_usd DESC
LIMIT 50
"""


def get_live_view():
    """Fetch the live shortfall view."""
    rows = execute_query(VIEW_QUERY)
    # Record trigger event
    record_event(
        event_type="view_refresh",
        trigger_source="scheduled_scan",
        details={"rows_returned": len(rows), "timestamp": datetime.now(timezone.utc).isoformat()}
    )
    return rows


def format_view_table(rows):
    """Format shortfall rows into a display table."""
    if not rows:
        return "No shortfalls detected."
    headers = ["Store", "City", "Product", "On-Hand", "Exposure $", "Days Cover", "Status", "Recovery"]
    table_data = []
    for r in rows:
        status_icon = "🔴" if r["position_status"] == "stockout" else "🟡"
        recovery_icon = "✅" if r["recovery_status"] == "approved" else ("⏳" if r["recovery_status"] == "proposed" else "—")
        table_data.append([
            r["store_id"],
            r["city"],
            r["product_name"],
            r["on_hand_units"],
            f"${r['lost_sales_exposure_usd']:,.0f}",
            f"{r['days_of_cover']} days",
            f"{status_icon} {r['position_status']}",
            f"{recovery_icon} {r['recovery_status']}",
        ])
    return gr.Dataframe(headers=headers, value=table_data)


# ═══════════════════════════════════════════════════════════════════════════════
# LAKEBASE SEARCH — retrieves from Build 1 BM25 index (not a separate store)
# ═══════════════════════════════════════════════════════════════════════════════
def search_product_context(query_text: str, top_k: int = 5) -> list:
    """Retrieve relevant product context using the Build 1 Lakebase BM25 Search index.

    Uses the idx_product_search_bm25 index created in Build 1 via the
    lakebase_text extension. This is the ONLY retrieval path for the
    assistant — no separate vector store is used.
    """
    results = execute_query(
        """SELECT product_id, product_name, category, subcategory, description,
               search_vector <@> to_bm25query(
                   to_tsvector('english', %s),
                   'northpeak_app.idx_product_search_bm25'
               ) AS relevance_score
        FROM northpeak_app.product_search
        ORDER BY relevance_score
        LIMIT %s""",
        (query_text, top_k)
    )
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — ASSIST: AI explanation, what-if, memo drafting
# ═══════════════════════════════════════════════════════════════════════════════
def get_shortfall_context(store_id: str, product_id: str) -> dict:
    """Gather all context for a shortfall from Lakebase.

    Combines operational data with product knowledge retrieved via the
    Build 1 Lakebase Search index (BM25).
    """
    # Current position
    position = execute_query(
        """SELECT * FROM northpeak.synced_inventory
           WHERE store_id = %s AND product_id = %s
           ORDER BY snapshot_date DESC LIMIT 7""",
        (store_id, product_id)
    )
    # Nearest surplus stores
    surplus = execute_query(
        """SELECT store_id, store_name, city, on_hand_units, store_lat, store_lng
           FROM northpeak.synced_inventory
           WHERE product_id = %s
             AND snapshot_date = (SELECT MAX(snapshot_date) FROM northpeak.synced_inventory WHERE product_id = %s)
             AND on_hand_units > 100
           ORDER BY on_hand_units DESC LIMIT 5""",
        (product_id, product_id)
    )
    # Substitutes
    substitutes = execute_query(
        """SELECT ps.*, p.product_name AS substitute_name, p.price_usd AS sub_price
           FROM northpeak_app.product_substitutes ps
           JOIN northpeak.synced_products p ON p.product_id = ps.substitute_product_id
           WHERE ps.product_id = %s AND ps.is_active = true""",
        (product_id,)
    )

    # --- Lakebase Search retrieval (Build 1 BM25 index) ---
    # Build a natural-language search query from the product context
    product_name = position[0]["product_name"] if position else product_id
    category = position[0].get("category", "") if position else ""
    search_query = f"{product_name} {category}".strip()
    search_results = search_product_context(search_query, top_k=5)

    return {
        "position_history": position,
        "surplus_stores": surplus,
        "substitutes": substitutes,
        "search_results": search_results,  # From Build 1 Lakebase Search index
    }


def explain_shortfall(store_id: str, product_id: str, session_id: str) -> str:
    """Explain why a store is flagged — grounded in actual records + Lakebase Search."""
    t0 = time.time()
    ctx = get_shortfall_context(store_id, product_id)
    pos = ctx["position_history"]
    surplus = ctx["surplus_stores"]
    search_hits = ctx["search_results"]

    # Build grounded context for the LLM
    history_text = "\n".join([
        f"  {r['snapshot_date']}: on_hand={r['on_hand_units']}, on_order={r['on_order_units']}"
        for r in pos[:7]
    ])
    surplus_text = "\n".join([
        f"  {r['store_id']} ({r['city']}): {r['on_hand_units']} units"
        for r in surplus[:5]
    ])

    # Context from Lakebase Search index (Build 1 BM25 retrieval)
    search_text = "\n".join([
        f"  [{r['product_id']}] {r['product_name']} ({r['category']}): {r['description'][:120]}"
        for r in search_hits[:5]
    ]) if search_hits else "  No related products found."

    system_prompt = """You are a retail operations analyst. Explain inventory shortfalls concisely.
    Ground every claim in the data provided. Be specific about numbers and trends.
    Use the related product context from search to explain category demand patterns.
    Keep response under 200 words."""

    user_msg = f"""Explain why {store_id} is flagged for {product_id}.

Recent inventory history (last 7 days):
{history_text}

Nearest surplus stores:
{surplus_text}

Related products (retrieved from Lakebase Search index):
{search_text}

Product: {pos[0]['product_name'] if pos else 'Unknown'} at ${pos[0]['price_usd'] if pos else 0}
Store: {pos[0]['store_name'] if pos else 'Unknown'} in {pos[0]['city'] if pos else 'Unknown'}"""

    response = call_llm(system_prompt, user_msg)
    latency = int((time.time() - t0) * 1000)

    # Log with search_refs showing retrieval from Lakebase Search index
    search_refs = [r["product_id"] for r in search_hits[:5]] if search_hits else []
    log_assist(session_id, "explanation", user_msg, response,
              latency_ms=latency,
              context_refs=[store_id, product_id, "lakebase_search:idx_product_search_bm25"] + search_refs)
    return response


def what_if_scenario(store_id: str, product_id: str, scenario: str, session_id: str) -> str:
    """Answer what-if questions grounded in data + Lakebase Search retrieval."""
    t0 = time.time()
    ctx = get_shortfall_context(store_id, product_id)
    pos = ctx["position_history"]
    surplus = ctx["surplus_stores"]
    subs = ctx["substitutes"]
    search_hits = ctx["search_results"]

    # Also search the scenario text to find relevant products
    scenario_search = search_product_context(scenario, top_k=3)

    # Combine search results (dedup by product_id)
    seen_ids = {r["product_id"] for r in search_hits} if search_hits else set()
    combined_search = (search_hits or []) + [
        r for r in (scenario_search or []) if r["product_id"] not in seen_ids
    ]

    # Search context from Lakebase BM25 index
    search_text = "\n".join([
        f"  [{r['product_id']}] {r['product_name']} ({r['category']}): {r['description'][:100]}"
        for r in combined_search[:5]
    ]) if combined_search else "  No related products found."

    system_prompt = """You are a retail operations analyst answering what-if scenarios.
    Use the actual data to compute outcomes. Show your math. Be concise (under 200 words).
    Consider: transfer cost ~$2/unit/100km, expedite cost ~$5/unit, substitute margin loss ~15%.
    Use the related product search results to identify viable substitutes or alternatives."""

    context = f"""Store: {store_id} ({pos[0]['city'] if pos else '?'})
Product: {product_id} ({pos[0]['product_name'] if pos else '?'}) at ${pos[0]['price_usd'] if pos else 0}/unit
Current on-hand: {pos[0]['on_hand_units'] if pos else 0}
Daily velocity: ~{max(1, (pos[-1]['on_hand_units'] - pos[0]['on_hand_units']) // max(len(pos)-1, 1)) if len(pos) > 1 else 5} units/day

Surplus stores:
{chr(10).join(f'  {r["store_id"]} ({r["city"]}): {r["on_hand_units"]} units' for r in surplus[:3])}

Substitutes:
{chr(10).join(f'  {s["substitute_name"]} (${s["sub_price"]}, confidence: {s["confidence_score"]})' for s in subs)}

Related products (retrieved from Lakebase Search index):
{search_text}"""

    user_msg = f"""Given this context:\n{context}\n\nWhat-if scenario: {scenario}"""
    response = call_llm(system_prompt, user_msg)
    latency = int((time.time() - t0) * 1000)

    search_refs = [r["product_id"] for r in combined_search[:5]] if combined_search else []
    log_assist(session_id, "what_if", user_msg, response,
              latency_ms=latency,
              context_refs=[store_id, product_id, scenario, "lakebase_search:idx_product_search_bm25"] + search_refs)
    return response


def draft_memo(store_id: str, product_id: str, move_type: str,
              source_store_id: str, units: int, session_id: str) -> str:
    """Auto-draft the recovery action memo, enriched by Lakebase Search context."""
    t0 = time.time()
    ctx = get_shortfall_context(store_id, product_id)
    pos = ctx["position_history"]
    surplus = ctx["surplus_stores"]
    search_hits = ctx["search_results"]

    # Find source store info
    source_info = next((s for s in surplus if s["store_id"] == source_store_id), None)
    price = pos[0]["price_usd"] if pos else 249
    recaptured = units * price * 0.8  # 80% sell-through assumption

    # Product description from Lakebase Search for better memo context
    product_desc = ""
    if search_hits:
        match = next((r for r in search_hits if r["product_id"] == product_id), None)
        if match:
            product_desc = match["description"]
        elif search_hits[0]:
            product_desc = f"Related: {search_hits[0]['product_name']} - {search_hits[0]['description'][:100]}"

    system_prompt = """You are drafting a formal inventory recovery action memo.
    Be concise, professional, and include all key numbers. Format as a ready-to-approve memo.
    Include: action type, source/destination, units, expected revenue impact, rationale.
    Reference the product description to justify urgency."""

    user_msg = f"""Draft a {move_type} memo:
- Destination: {store_id} ({pos[0]['store_name'] if pos else '?'})
- Product: {product_id} ({pos[0]['product_name'] if pos else '?'})
- Product description (from Lakebase Search): {product_desc}
- Move type: {move_type}
- Source: {source_store_id} ({source_info['city'] if source_info else '?'}, {source_info['on_hand_units'] if source_info else '?'} units surplus)
- Units to move: {units}
- Unit price: ${price}
- Expected recaptured revenue: ${recaptured:,.0f}
- Current on-hand at destination: {pos[0]['on_hand_units'] if pos else 0}
- Daily velocity: ~5 units/day"""

    response = call_llm(system_prompt, user_msg)
    latency = int((time.time() - t0) * 1000)

    search_refs = [r["product_id"] for r in search_hits[:5]] if search_hits else []
    log_assist(session_id, "draft_memo", user_msg, response,
              latency_ms=latency,
              context_refs=[store_id, product_id, move_type, "lakebase_search:idx_product_search_bm25"] + search_refs)
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — ACT: Propose, approve, commit
# ═══════════════════════════════════════════════════════════════════════════════
def propose_action(store_id: str, product_id: str, move_type: str,
                   source_store_id: str, units: int, reason: str,
                   predicted_recaptured: float) -> dict:
    """Write a proposed action to the writable Postgres table."""
    result = execute_write(
        """INSERT INTO northpeak_app.transfer_actions
           (source_store_id, destination_store_id, product_id,
            units_requested, status, requested_by, reason, created_at, updated_at)
           VALUES (%s, %s, %s, %s, 'proposed', 'northpeak_ops_app', %s, NOW(), NOW())
           RETURNING action_id, created_at""",
        (source_store_id, store_id, product_id, units, reason)
    )
    action_id = str(result[0]["action_id"]) if result else str(uuid.uuid4())

    # Record in workflow state
    record_event(
        event_type="action_proposed",
        entity_type="transfer_action",
        entity_id=action_id,
        trigger_source="assistant_recommendation",
        details={
            "store_id": store_id, "product_id": product_id,
            "move_type": move_type, "source_store_id": source_store_id,
            "units": units, "predicted_recaptured_usd": predicted_recaptured,
        }
    )
    return {"action_id": action_id, "status": "proposed"}


def approve_action(action_id: str, approver: str) -> dict:
    """Approve a proposed action — human in the loop."""
    execute_write(
        """UPDATE northpeak_app.transfer_actions
           SET status = 'approved', approved_by = %s,
               approved_at = NOW(), updated_at = NOW(),
               units_approved = units_requested
           WHERE action_id = %s::uuid AND status = 'proposed'""",
        (approver, action_id)
    )
    # Record approval in workflow state
    record_event(
        event_type="action_approved",
        entity_type="transfer_action",
        entity_id=action_id,
        trigger_source="human_approval",
        details={"approver": approver, "approved_at": datetime.now(timezone.utc).isoformat()}
    )
    return {"action_id": action_id, "status": "approved", "approved_by": approver}


def commit_action(action_id: str) -> dict:
    """Commit an approved action — closes the loop."""
    execute_write(
        """UPDATE northpeak_app.transfer_actions
           SET status = 'committed', committed_at = NOW(), updated_at = NOW()
           WHERE action_id = %s::uuid AND status = 'approved'""",
        (action_id,)
    )
    # Record commit in workflow state
    record_event(
        event_type="action_committed",
        entity_type="transfer_action",
        entity_id=action_id,
        trigger_source="system_commit",
        details={"committed_at": datetime.now(timezone.utc).isoformat()}
    )
    return {"action_id": action_id, "status": "committed"}


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULED TRIGGER — refreshes scoring periodically
# ═══════════════════════════════════════════════════════════════════════════════
def scheduled_refresh():
    """Background thread that triggers view refresh every 5 minutes."""
    while True:
        try:
            record_event(
                event_type="scheduled_trigger",
                trigger_source="background_timer",
                details={"interval_sec": 300}
            )
        except Exception as e:
            print(f"Scheduled trigger error: {e}")
        time.sleep(300)


# Start background trigger
refresh_thread = threading.Thread(target=scheduled_refresh, daemon=True)
refresh_thread.start()


# ═══════════════════════════════════════════════════════════════════════════════
# GRADIO UI
# ═══════════════════════════════════════════════════════════════════════════════
def refresh_view():
    """Refresh the live view and return as dataframe."""
    try:
        rows = get_live_view()
    except Exception as e:
        return [[f"Error: {type(e).__name__}: {str(e)[:100]}"]+[""]*7]
    if not rows:
        return [["No shortfalls"]*8]
    table_data = []
    for r in rows:
        status_icon = "🔴" if r["position_status"] == "stockout" else "🟡"
        recovery_icon = "✅" if r["recovery_status"] in ("approved", "committed") else ("⏳" if r["recovery_status"] == "proposed" else "—")
        table_data.append([
            r["store_id"], r["city"], r["product_name"],
            str(r["on_hand_units"]),
            f"${float(r['lost_sales_exposure_usd']):,.0f}",
            f"{r['days_of_cover']} d",
            f"{status_icon} {r['position_status']}",
            f"{recovery_icon} {r['recovery_status']}",
        ])
    return table_data


def run_hero_flow():
    """Execute the full hero question flow end-to-end."""
    session_id = str(uuid.uuid4())[:8]
    store_id = "STORE-0214"
    product_id = "SKU-APP-04412"
    source_store_id = "STORE-0377"
    units = 60

    results = []

    # Step 1: Explain
    explanation = explain_shortfall(store_id, product_id, session_id)
    results.append(f"**📊 Explanation:**\n{explanation}")

    # Step 2: What-if
    whatif = what_if_scenario(
        store_id, product_id,
        "What if we transfer 60 units from STORE-0377 (Colorado Springs)?",
        session_id
    )
    results.append(f"\n**🔮 What-If Analysis:**\n{whatif}")

    # Step 3: Draft memo
    memo = draft_memo(store_id, product_id, "transfer", source_store_id, units, session_id)
    results.append(f"\n**📝 Drafted Memo:**\n{memo}")

    # Step 4: Propose action
    recaptured = units * 249 * 0.8
    proposal = propose_action(
        store_id, product_id, "transfer", source_store_id, units,
        f"Recovery transfer: {units} units Summit Down Parka from Colorado Springs to Denver",
        recaptured
    )
    results.append(f"\n**📋 Action Proposed:** ID = {proposal['action_id']}")

    return "\n".join(results), proposal["action_id"], session_id


def approve_hero(action_id: str, approver_name: str):
    """Approve and commit the hero action."""
    if not action_id:
        return "No action to approve. Run the hero flow first."
    if not approver_name:
        approver_name = "dana.ruiz@northpeak.com"

    # Approve
    result = approve_action(action_id, approver_name)

    # Commit
    commit_result = commit_action(action_id)

    return f"""✅ **Action Approved & Committed**
- Action ID: {action_id}
- Approved by: {approver_name}
- Status: {commit_result['status']}
- The transfer is now recorded and the live view will reflect the recovery."""


# Build the Gradio interface
with gr.Blocks(title="NorthPeak Store Ops", theme=gr.themes.Soft()) as app:
    gr.Markdown("# 🏔️ NorthPeak Store Ops — Inventory Recovery Console")
    gr.Markdown("*Live shortfalls ranked by revenue at risk. AI-assisted recovery recommendations.*")

    with gr.Tab("📊 Live View"):
        gr.Markdown("### Shortfalls — Ranked by Lost Sales Exposure")
        view_table = gr.Dataframe(
            headers=["Store", "City", "Product", "On-Hand", "Exposure", "Cover", "Status", "Recovery"],
            interactive=False,
        )
        refresh_btn = gr.Button("🔄 Refresh View", variant="primary")
        refresh_btn.click(fn=refresh_view, outputs=view_table)

    with gr.Tab("🤖 Assist"):
        gr.Markdown("### AI-Powered Recovery Assistant")
        gr.Markdown("Ask questions about shortfalls, run what-if scenarios, or get action recommendations.")

        with gr.Row():
            store_input = gr.Textbox(value="STORE-0214", label="Store ID")
            product_input = gr.Textbox(value="SKU-APP-04412", label="Product ID")

        with gr.Accordion("Explain Shortfall", open=True):
            explain_btn = gr.Button("🔍 Explain Why Flagged")
            explain_output = gr.Markdown()
            explain_btn.click(
                fn=lambda s, p: explain_shortfall(s, p, str(uuid.uuid4())[:8]),
                inputs=[store_input, product_input],
                outputs=explain_output
            )

        with gr.Accordion("What-If Scenario", open=True):
            scenario_input = gr.Textbox(
                value="What if we transfer 20 units from the nearest surplus store?",
                label="Scenario"
            )
            whatif_btn = gr.Button("🔮 Run What-If")
            whatif_output = gr.Markdown()
            whatif_btn.click(
                fn=lambda s, p, sc: what_if_scenario(s, p, sc, str(uuid.uuid4())[:8]),
                inputs=[store_input, product_input, scenario_input],
                outputs=whatif_output
            )

        with gr.Accordion("Draft Recovery Memo", open=False):
            with gr.Row():
                source_input = gr.Textbox(value="STORE-0377", label="Source Store")
                units_input = gr.Number(value=60, label="Units")
            memo_btn = gr.Button("📝 Draft Memo")
            memo_output = gr.Markdown()
            memo_btn.click(
                fn=lambda s, p, src, u: draft_memo(s, p, "transfer", src, int(u), str(uuid.uuid4())[:8]),
                inputs=[store_input, product_input, source_input, units_input],
                outputs=memo_output
            )

    with gr.Tab("⚡ Act"):
        gr.Markdown("### Recovery Action — Propose → Approve → Commit")
        gr.Markdown("Run the full hero flow to demonstrate the closed loop.")

        hero_btn = gr.Button("🚀 Run Hero Flow (Store 214 → Summit Down Parka)", variant="primary")
        hero_output = gr.Markdown()
        action_id_state = gr.State(value="")
        session_id_state = gr.State(value="")

        hero_btn.click(
            fn=run_hero_flow,
            outputs=[hero_output, action_id_state, session_id_state]
        )

        gr.Markdown("---")
        gr.Markdown("### Approve & Commit")
        with gr.Row():
            approver_input = gr.Textbox(value="dana.ruiz@northpeak.com", label="Approver")
        approve_btn = gr.Button("✅ Approve & Commit Transfer", variant="secondary")
        approve_output = gr.Markdown()

        approve_btn.click(
            fn=approve_hero,
            inputs=[action_id_state, approver_input],
            outputs=approve_output
        )

    # Auto-refresh on load
    app.load(fn=refresh_view, outputs=view_table)


if __name__ == "__main__":
    port = int(os.environ.get("DATABRICKS_APP_PORT", 8000))
    app.launch(server_name="0.0.0.0", server_port=port)
