# Data Model

> **This is the Build 1 (Lakebase) answer key.** The enablement scenario's Build 1 asks teams to sync a governed UC table into Lakebase AND model a writable operational table — because a UC synced table is **read-only** in Postgres (only SELECT / CREATE INDEX / DROP TABLE), so the app's write actions need a separate writable table next to it. This spec encodes exactly that: one **synced read-only** position table + one **writable** ops-actions table.

## Two stores

- **Delta tables** — lakehouse source of truth, read-only from the app. SQL Warehouse queries + Genie read here.
- **Lakebase Postgres** — the low-latency serving + write surface: chat state + a synced read-only mirror of the position/recommendation data the store-ops screen reads + a writable table the app records recovery actions to.

## Lakebase schema (`app.*`)

### Chat state (reusable — keep as-is across demos)

| Table | Key fields |
|-------|-----------|
| `conversations` | id, userEmail, title, kind (`demo_dock` / `default`), timestamps |
| `messages` | conversationId, role, content, position, traceId (MLflow), thinking (JSONB — tool calls + reasoning for reload-safe history), error |
| `feedback` | messageId, value (`up`/`down`), rationale, traceId, mlflowAssessmentId |

### Synced read-only mirror (from Delta — NorthPeak-specific)

These mirror Gold/Delta tables and are **read-only from the app** (in Build 1 terms, UC synced tables). The app SELECTs from them for sub-ms per-store reads; it never writes them.

| Table | Source (Delta) | Key fields |
|-------|--------|-----------|
| `store_sku_position` | `gold_store_sku_position` | storeId, storeName, region, **climateZone**, city, **storeLat**, **storeLng** (DOUBLE PRECISION — drives the Operations store map), productId, productName, category, seasonality, onHandUnits, onOrderUnits, recentUnits7d, avgDailyVelocity, weeksOfSupply, priceUsd, markdownRiskScore (0–1 from `ai_classify`, pass-through), **positionStatus** (`stockout`/`at_risk`/`overstock`/`healthy` — the UI colors the map + badges by this) |
| `open_shortfalls` | `gold_open_shortfalls` | storeId (PK part), productId (PK part), onHandUnits, avgDailyVelocity, lostSalesExposureUsd, nearestSurplusStoreId, nearestSurplusOnHand, nearestSurplusDistanceKm |
| `recovery_recommendations` | `gold_recovery_recommendations` (built by the SDP pipeline heuristic; optionally overwritten by the ML model in `03-ml-recovery.md`) | storeId (PK part), productId (PK part), recommendedMove (`transfer`/`expedite`/`substitute`), recommendedSourceStoreId, recommendedSubstituteProductId, recommendedUnits, predictedRecapturedUsd (double), predictedNetValueUsd (double), moveRanking (JSONB — all three options with predicted recaptured $ + net $ + cost), scoredAt (timestamp) |
| `products` | `raw_products` (synced from Delta) | **productId** (PK), productName, category, subcategory, priceUsd, seasonality, **description** (STRING — short searchable text on material/features/warmth for matching substitutes), launchDate, isActive. Indexed by **Lakebase Search** (Milestone 2) for hybrid text/vector retrieval over (name, description). |

The `recovery_recommendations` table is **read-only from the app** — a copy of the model's predictions kept in Lakebase so the agent's `rank_recovery_moves` lookup is sub-second. The model itself lives in Unity Catalog (`{catalog}.{schema}.recovery_recommender`, `@prod`); the app never calls it directly. `moveRanking` (JSONB) is what powers the ranked-options list + the arithmetic what-if in the drawer.

The `products` table is a **read-only synced mirror** of the raw product catalog. Unlike `store_sku_position` (which reflects current inventory), `products` is relatively static (catalog changes rarely) and serves two purposes: (1) the **product search** affordance in the UI uses it to populate a substitute-candidate lookup, (2) the agent's `search_products` tool queries it via **Lakebase Search** to find comparable in-stock items when ranking the **substitute** recovery move. **Lakebase Search** is a Milestone-2 Lakebase capability (hybrid text/vector indexes over the product name + description fields); the app's `search_products` tool issues hybrid search queries to find products by semantic similarity (e.g., "insulated warm jacket" matches the Ridgeline + Timberline warm-layer alternatives).

### Writable operational table (app writes here — the Build 1 writable-table requirement)

| Table | Written by | Key fields |
|-------|-----------|-----------|
| `ops_actions` | the app / agent's `execute_recovery_action` | id (PK), storeId, productId, moveType (`transfer`/`expedite`/`substitute`/`markdown_hold`), sourceStoreId (nullable), units, draftedRequest (text — the transfer/markdown memo the agent wrote), predictedRecapturedUsd, status (`proposed`/`approved`/`executed`/`overridden`), approvedBy (userEmail, OBO-stamped), **auditTrail** (append-only JSONB array), createdAt, decidedAt |

`ops_actions` is the **only** table the app writes. An approved transfer inserts/updates a row here (move + drafted request + who approved); a markdown-hold on the source surplus inserts a paired `markdown_hold` action row. The Operations queue derives a position's live state by LEFT JOIN-ing `store_sku_position` → its latest `ops_actions` row (so "recovery in progress" + the move badge come from the writable table, and the read-only synced position is never mutated). The append-only `auditTrail` makes each action row a standalone timeline the drawer's Activity tab renders from one read.

## Delta → Lakebase sync

> **Talking-track vs build:** in production this is **Lakebase Synced Tables** — managed, continuous Delta→Lakebase replication with the same UC governance ("the Gold tables your pipeline produces are synced into Lakebase automatically"). For the demo build we keep it simple: a manual one-shot sync at boot, code we can show, no extra resource. Same outcome on screen. (In the enablement build, teams set up the actual synced table — this manual sync is the app-template's stand-in so the demo boots without the Build-1 wiring.)

1. If synced mirror tables empty → pull via Databricks SQL Statements API: `store_sku_position` (the affected + a sample of everyday positions), `open_shortfalls`, `recovery_recommendations` for the same shortfall set, and the **`products`** catalog (all products — small, static; feeds the product search / substitute lookup).
2. Chunked inserts (2000/batch), idempotent (skip on conflict).
3. `ops_actions` is **not** synced (it's the app's own writable state) — it starts empty.
4. "Reset demo" button → clean slate: truncate `ops_actions` + re-sync the read-only mirrors. **All agent writes are wiped** — every recovery action clears, shortfalls return to `stockout`/`at_risk`, KPI exposure returns to full. Intentional: between presentations Dana wants the backlog to look untouched.

Source tables from `config/app.json` `data.tables` (maps logical names → Delta table names, used by sync + analytics queries).

## Lakebase provisioning

1. Create Lakebase Postgres project + database in the workspace.
2. Wire into `app.yaml` → Lakebase plugin resolves host + credentials at runtime.
3. Auth: SDK chain (CLI profile dev, OBO prod). `databricks apps run-local` injects env vars from the bound resource.
4. Schema: Drizzle ORM, migrations from `server/db/schema.ts`, auto-applied on boot.
