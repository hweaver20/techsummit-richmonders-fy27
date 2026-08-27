# App Specification — Overview, Home & Assistant

> **Build-time note.** Read `DEMO_SKILL_DIR/app/app.md` FIRST and follow it end-to-end (rsync template → customize → Lakebase → env → smoke test → deploy). This is **not** a from-scratch build: the template at `DEMO_SKILL_DIR/app/app_template/` is a Node.js + React + Express (`@databricks/appkit`) app with Lakebase, agent streaming, MLflow tracing, OBO auth, chat dock, and scripted demo chain already wired. Rsync it into `PROJECT/app/`, read `TEMPLATE_MAP.md` for what's preserved vs customized, then rewrite domain pieces (home narrative, agent tools, Lakebase schema, analytics SQL, theming) to match this story. On conflict: `app.md` governs *how*, this spec governs *what*.

> **This app maps 1:1 to the enablement build arc.** It is the concrete shape of the three-milestone challenge: **Build 1 (Lakebase)** = the data model in `03_DATA_MODEL.md` (a synced read-only position table + a writable ops-actions table); **Build 2 (Databricks Apps)** = this app's three layers **Visualize → Assist → Act**; **Build 3 (Unity AI Gateway)** = the assistant's model calls run through the Gateway (spend cap, guardrails, per-store attributable inference logging) — talk-track in the app, the "hero question" the whole thing answers is *"Store 214 is short on a top product — what's the best recovery move?"*.

## Pitch

AI assistant that **investigates a store shortfall, ranks the recovery move, and executes it** in one conversation — not just answers questions. Dana watches every step happen live: the assistant asks Genie to investigate why Store 214 is at zero on the Summit Down Parka, reads the live Lakebase position + the nearest surplus, then **looks up the ranked recovery recommendation** (`app.recovery_recommendations`, mirrored from the `gold_recovery_recommendations` table the SDP pipeline builds via a heuristic — optionally replaced by an ML model, `03-ml-recovery.md`) to rank the three plays — transfer / expedite / substitute — each with cost, margin, and predicted recaptured revenue. It explains *why* transfer wins (a close southern surplus store holds the units), offers a what-if, drafts the transfer request, and **stops for approval**. Dana approves → the transfer + a markdown-hold flag write to Lakebase → the Operations queue + KPI tiles tick live. Every action is traced in MLflow and every model call is governed by Unity AI Gateway.

## Databricks capabilities mapped

| Capability | Where it shows |
|-----------|---------------|
| **Lakebase** | The read surface (synced read-only `store_sku_position` for low-latency per-store reads) AND the write surface (writable `ops_actions` — the app records approved transfers/holds here; a synced UC table is read-only in Postgres, so the app writes to its own table). Same UC governance as Delta. |
| **AI/BI Genie** | `ask_data` tool routes the "why is this store short?" investigation to the Genie space; reasoning streams into the Thinking panel. |
| **ML model (UC-registered)** | The `recovery_recommender` model's batch output feeds the agent's ranking — `app.recovery_recommendations(store_id, product_id, recommended_move, predicted_recaptured_usd, predicted_net_value_usd, move_ranking, …)` is one of the mirrored tables. The app never calls the model directly; it reads the predictions. |
| **AI Functions (`ai_classify`)** | Markdown-risk score (0–1) extracted in SDP from each position's `merch_note_text`, mirrored on the position row. The overstock/markdown view is sortable by markdown risk. |
| **Unity AI Gateway** | The assistant's model endpoint is registered through the Gateway — spend cap (~$200K/yr bounded), read guardrails, every call logged to a UC inference table and attributable **per store**. Talk-track surfaced via a small "AI spend" panel/link. |
| **MLflow tracing** | Per-turn traces with tool spans. Thumbs up/down → human assessments on traces. |
| **Databricks Apps** | SSO, OBO auth (actions stamped with Dana's email), secrets, auto-scaling. |
| **AI/BI Dashboards** | Embedded as an iframe with SSO — the stockout/markdown dashboard from `04-ai-bi.md`. |

## Pages

| Page | Purpose | Key capability |
|------|---------|---------------|
| **Home** | Narrative landing — story, persona, journey diagram, starter chips, featured action card, activity feed | Config-driven (`config/app.json`) |
| **Operations** | The store-shortfall surface — a store map + a shortfall queue, KPI cards (Lost-sales exposure / Markdown exposure / Open shortfalls), detail drawer with the ranked recovery options + Approve/Override + activity timeline | **Lakebase** OLTP |
| **Analytics** | Warehouse-backed charts: sell-through trend on the affected SKUs, worst shortfalls, per-climate-zone position mix | **SQL Warehouse** on Delta |
| **Dashboard** | Embedded AI/BI dashboard iframe (from `04-ai-bi.md`) | **AI/BI Dashboards** |

## Assistant

Lives on every page. Two surfaces, one brain:
- **Floating dock** (bottom-right) — persistent conversation per user (`kind='demo_dock'`), survives navigation. Hidden on the full-page chat route.
- **Full-page chat** — for longer conversations or reviewing history.

### The three layers (Visualize / Assist / Act)

This is the enablement arc rendered in the app:
- **Visualize** (Operations page) — the live store×SKU shortfall map + queue makes the important thing obvious at a glance: red northern stockouts next to amber southern surplus. Reads synced Lakebase position data.
- **Assist** (the agent) — a chat assistant that explains why a store is flagged, ranks the recovery move, and offers a what-if. Reads the recovery model's recommendation + the live position.
- **Act** (the write) — after human approval, the app writes the chosen move (transfer/expedite/substitute + a markdown-hold) to the writable Lakebase `ops_actions` table; the Operations page cascades.

### Thinking panel
Top-right floating panel, streams live during agent turns: reasoning steps, the Genie investigation ("querying store positions", "found nearest surplus"), tool calls with inputs/results. Persisted on the message as `thinking[]` JSONB → survives reload (collapsed "Reasoning · N tools" toggle).

### Human-in-the-loop
**Read-only queries** — assistant calls Genie / reads Lakebase, synthesizes an answer. No side effects.

**Action chains** — strict 3-phase:
1. **Discover** — read the shortfall (store, SKU, on_hand=0, velocity), read the nearest surplus, **look up the ranked recovery recommendation** for this (store, SKU) (read-only).
2. **Draft + confirm** — present the ranked options (transfer/expedite/substitute) each with units, cost, margin impact, and predicted recaptured $; recommend the top one and explain why; offer a what-if ("what if 40 units instead of 60?"); draft the transfer request text → **STOP, wait for approval**.
3. **Execute** (after "yes") — write the approved move to `ops_actions` (records move_type, source/dest store, units, the drafted request, predicted recaptured $), append an audit entry, and set a markdown-hold flag on the source surplus position's action record — one atomic write.

### Agent tools (NorthPeak)

The agent has five tools, chained so the demo loop is visible: (1) **ask Genie** to investigate, (2) **read Lakebase** for the live shortfall + surplus context, (3) **search the product catalog** to find comparable in-stock items for the substitute move, (4) **read the ML recommendation** in Lakebase to rank the move, (5) **write Lakebase** atomically after approval.

| Tool | What it does | Phase |
|------|-------------|-------|
| `ask_data` | Delegates to the Genie space — investigates the shortfall over the governed lakehouse, streams reasoning to the Thinking panel | Investigation |
| `find_shortfall` | Queries Lakebase: the open shortfall for a `{store_id, product_id}` (or the worst open shortfall) — on_hand, recent velocity, lost-sales exposure, and the nearest surplus store + its on-hand + distance | Discovery |
| `search_products` | Queries Lakebase Search over the product catalog (`products` table: name + description) to find comparable in-stock items matching a search query. Returns ranked candidates with product_id, product_name, category, price_usd, on_hand_units. **Powers the substitute recovery option** — when ranking moves, the agent calls this tool to find a comparable available product to offer the customer instead of the sold-out item. Uses hybrid text/vector retrieval over the product catalog indexed in Lakebase Postgres. | Discovery (substitute context) |
| `rank_recovery_moves` | Queries Lakebase `app.recovery_recommendations` for the `{store_id, product_id}` — returns the model's `recommended_move`, `predicted_recaptured_usd`, `predicted_net_value_usd`, and the full `move_ranking` (all three options with their predicted recaptured $ + net $ + cost). **This is the demo's "ML in the loop" moment** — the agent quotes the ranked options + the recommended move in the draft, and recomputes the what-if arithmetically from `move_ranking`. | Discovery |
| `execute_recovery_action` | Bulk/atomic write to Lakebase `app.ops_actions`: records the approved move (move_type, from/to store, units, drafted request, predicted recaptured $), appends an audit entry, sets a markdown-hold flag on the source surplus. Inputs are a FILTER (`{store_id, product_id, move_type, units, source_store_id?}`) + the drafted request text — never a list of IDs. | Execution (requires approval) |

> **Write tools must trigger a visible UI refresh.** `execute_recovery_action` MUST publish a `dataMutated` event on commit. The Operations page subscribes and refetches: the Open-shortfalls KPI ticks down, the affected shortfall row flips to "recovery in progress" and gains a move badge (Transfer / Expedite / Substitute), the store map's red dot for the store turns neutral, the lost-sales-exposure KPI drops by the predicted recaptured $, and any open drawer re-fetches its activity timeline. The user must **see** the queue change without reloading — that live cascade is the moment the demo lands.

## Home page

Narrative landing — tells the story in 10s, plays it in 90s.

**Story section:** Persona badge ("Dana Ruiz · SVP Retail Operations · NorthPeak Retail"), headline ("Sold out in the North, dead stock in the South"), situation (an early cold snap ~3 weeks ago flipped cold-weather-apparel demand — ~30 northern stores at zero on the same 5 SKUs while ~40 southern stores sit on surplus; ~$4.8M lost-sales exposure, ~$5.6M markdown clock — *regional managers pinged her this morning*), goal (find the worst shortfalls → get the recovery move → approve it), preview bullets.

**Journey diagram:** 4-beat horizontal strip — See the shortfalls → Operations | Ask why Store 214 is short → starts chat | Rank the recovery move → the model | Approve the transfer → action flow.

**Starter chips:** "Where are we short and where are we over-stocked?" / "Why is Store 214 out of the Summit Down Parka?" / "What's the best recovery move for Store 214?" — each starts a fresh conversation.

**Featured action card:** "Recommend a recovery move for Store 214 — rank transfer vs expedite vs substitute" — one click triggers the full investigate → rank → draft → approve flow.

**Activity feed:** Live tail of agent actions ("Approved transfer: 60 units of Summit Down Parka STORE-0377 → STORE-0214, predicted +$14K recaptured", "Set markdown hold on STORE-0377 surplus", "Ranked recovery for 3 shortfalls"). Auto-refreshes.

## Scripted demo flow (~3 min)

Assistant supports a scripted chain via `config.assistantScript`. After each response, a "Suggested next" chip appears if trigger keywords are detected in the previous answer.

**Step 1 — "Why is Store 214 out of the Summit Down Parka, and what are my options?"**
Always available. `ask_data` → Genie investigates: zero on-hand against three weeks of climbing sell-through, and the same SKU sitting over-stocked in a cluster of southern stores. `find_shortfall` reads the live position + nearest surplus (STORE-0377, Phoenix). Thinking panel shows the routing live. Suggests ranking the recovery move.

**Step 2 — "Rank the recovery move. Use the model."**
Unlocks when "short"/"shortfall"/"options"/"Store 214" in the previous answer. Agent calls `rank_recovery_moves` → quotes the ranked options. For the **substitute** option, calls `search_products` with a query like *"warm insulated jacket similar to Summit Down Parka"* to find the **Ridgeline Insulated Jacket** or **Timberline Fleece** as an in-stock alternative → "**Transfer ~60 units from STORE-0377 (Colorado Springs, 100 mi away)** — predicted +$14K recaptured, lowest cost, protects margin both ends. Expedite from DC: +$11K but higher cost, 4 days. Substitute the Ridgeline Jacket (in stock, similar warmth): +$6K, leaks margin." Drafts the transfer request. Shows the ranked list + the what-if slider. Stops and waits.

**Step 3 — "Yes — approve the transfer."**
Unlocks when "transfer"/"recover"/"approve" mentioned. `execute_recovery_action` runs one atomic write on Lakebase: records the transfer + drafted request, appends audit, sets the markdown-hold on STORE-0377's surplus. Then emits `dataMutated`. On screen: the Open-shortfalls KPI drops, Store 214's row flips to "recovery in progress" with a **Transfer** badge, the map's red Denver dot turns neutral, lost-sales exposure ticks down by ~$14K, and any open drawer re-fetches its timeline — all without Dana touching anything. **That live cascade is the story beat — confirm it works before demoing.**

**Performance:** Agent prompt steers toward narrow Genie questions (20–40s). The shortfall + recommendation lookups are Lakebase reads — sub-second.

All narrative config lives in `config/app.json` — persona, story, starter questions, assistantScript (with triggerAfter keywords), featuredAction, resource IDs. Read it directly.
