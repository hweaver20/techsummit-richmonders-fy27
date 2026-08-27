# AI/BI — Dashboard + Genie

Tables and columns referenced here are defined in `01-lakeflow.md` (Section B) and `03-ml-recovery.md` (the recommendations table).
Your goal is to create a Genie space and an AI/BI Dashboard for this story, respecting these specifications.

> **Talking-track-only products mentioned in the README** — do **not** build resources for these:
> - **Databricks One** is a workspace surface, not a buildable artifact — the dashboard + Genie space appear there once built.
> - **Genie Code** is the authoring assist inside the editor — narrative only.
> - **Unity Catalog** / **Unity AI Gateway** are governance layers — the app's model calls run through AI Gateway (talk-track for this data/analytics spec; the app spec covers the assistant).

> Parallelization + subagent spawning rules live in `SKILL.md` → **Parallelization with Subagents**.

## A. Genie Space

**Skill to use**: `databricks-genie` — read `SKILLS/databricks-genie/SKILL.md` before implementing.

Create `NorthPeak Store Operations` Genie Space.

### Tables

`mv_store_position` (canonical exposure metric view over `gold_store_sku_position` — lost-sales / markdown / counts — defined in `02-uc-governance.md`), `gold_store_sku_position` (per store×SKU current position: on_hand, recent velocity, weeks_of_supply, `position_status`, geo, climate — used for map + shortfall/surplus rollups via GROUP BY), `gold_open_shortfalls` (current shortfalls + nearest-surplus context), `gold_recovery_recommendations` (the ranked recovery move per shortfall + predicted recaptured $ — built by the pipeline heuristic in `01-lakeflow.md`, optionally by the ML model in `03-ml-recovery.md`), `raw_products` (catalog + seasonality), `raw_stores` (store master + climate zone + geo).

### Self-sufficient room

Anyone opening the Genie room must understand the story without prior context. Wire all three:

- **Space `description`** (set via `PATCH /api/2.0/genie/spaces/<id>`): 1-3 sentences naming the event (cold snap → cold-weather apparel sold out North / dead-stock South, same SKUs) + the headline exposure numbers + the recovery angle, pointing to the suggested questions in order. Lift it from the README.
- **Story-context `text_instruction`** at the TOP of `instructions.text_instructions[]`: WHAT HAPPENED · WHAT TO HELP DANA DO · TONE. ~5-8 lines. Honored every turn.
- **`sample_questions`** (chips) AND matching `example_question_sqls` walk the 7-step arc below, in the same order.

### Instructions

```
You analyze NorthPeak Retail store-operations data for Dana Ruiz (SVP Retail Operations, non-technical).

CONTEXT: An early cold snap ~3 weeks ago flipped demand for cold-weather apparel. The SAME 5 SKUs
(Summit Down Parka SKU-APP-04412 + 4 others) are STOCKED OUT in ~30 northern stores (real demand,
lost sales) while sitting as DEAD STOCK in ~40 southern stores (markdown clock running). One
misallocation, two symptoms.

BASELINES: A healthy position has weeks_of_supply between ~1 and ~8. position_status is the single
signal: 'stockout' (on_hand 0, still selling), 'at_risk' (<1 week supply), 'overstock' (>8 weeks +
high markdown risk), 'healthy'.

HEADLINE NUMBERS — always answer from mv_store_position (same definitions the dashboard tiles use):
- "How much are we losing to stockouts?" → MEASURE(lost_sales_exposure)
- "What's our markdown exposure?" → MEASURE(markdown_exposure)
- "How many positions are stocked out in the North?" → MEASURE(stockout_count) filtered climate_zone='North'

INVESTIGATION FLOW for "where are we short and where are we over-stocked, and why?":
1. mv_store_position → MEASURE(stockout_count) + MEASURE(overstock_count) by climate_zone → North = stockouts, South = overstock
2. gold_store_sku_position → the divergence is confined to the 5 cold_weather apparel SKUs (GROUP BY product_name, position_status)
3. gold_open_shortfalls WHERE store_id='STORE-0214' → the hero shortfall; note nearest_surplus_store_id holds the units
4. gold_recovery_recommendations → the model recommends a move (transfer/expedite/substitute) + predicted recaptured $
Conclude + suggest: "Want me to rank the recovery move for Store 214?"

RECOVERY FOLLOW-UP:
- "What's the best recovery move for Store 214's parka?" → gold_recovery_recommendations for that (store, SKU) → recommended_move + predicted_recaptured_usd + the move_ranking options.
- "How much could we recapture across all open shortfalls?" → SUM(predicted_recaptured_usd) from gold_recovery_recommendations.
- "How many shortfalls are best solved by transfer vs expedite?" → GROUP BY recommended_move.
```

### Sample Questions — 7-step story arc

Ship 7 questions, in this order, each as both a chip (`config.sample_questions`) AND a curated SQL (`instructions.example_question_sqls`):

1. **Headline** — "How much are we losing to stockouts, and what's our markdown exposure right now?" → `MEASURE(lost_sales_exposure)` + `MEASURE(markdown_exposure)` from `mv_store_position`.
2. **The split** — "Where are we short and where are we over-stocked? Break it down by climate zone." → `MEASURE(stockout_count)` + `MEASURE(overstock_count)` from `mv_store_position` GROUP BY `climate_zone`.
3. **Drill to SKUs** — "Which products are driving both problems?" → `gold_store_sku_position` GROUP BY `product_name`, `position_status` → the 5 cold-weather SKUs dominate stockout (North) and overstock (South).
4. **The hero store** — "Store 214 is short on the Summit Down Parka — how bad is it, and is there stock nearby?" → `gold_open_shortfalls WHERE store_id='STORE-0214' AND product_id='SKU-APP-04412'` → 0 on-hand, velocity, `nearest_surplus_store_id` holds units.
5. **The recommendation** — "What's the best recovery move for Store 214, and how much would it recapture?" → `gold_recovery_recommendations` for that (store, SKU) → `recommended_move = 'transfer'` from STORE-0377, `predicted_recaptured_usd`, the ranked options.
6. **Portfolio recovery** — "Across all open shortfalls, how much could we recapture, and by which move?" → `gold_recovery_recommendations` SUM(`predicted_recaptured_usd`) + GROUP BY `recommended_move`.
7. **Markdown side** — "Which southern stores are the biggest markdown risk on these SKUs?" → `gold_store_sku_position WHERE position_status='overstock'` ORDER BY `markdown_exposure_usd` DESC.

### Validation

"How much are we losing to stockouts?" → answered from `mv_store_position` (`MEASURE(lost_sales_exposure)`), matches the dashboard tile. "Where are we short/over-stocked?" → North stockouts + South overstock on the 5 SKUs. "Best move for Store 214?" → transfer from STORE-0377 with a recaptured-$ figure, from `gold_recovery_recommendations`. Add `genie_space_id` to `resources.json`.


## B. Dashboard

**Skill to use**: `databricks-aibi-dashboards` — read `SKILLS/databricks-aibi-dashboards/SKILL.md` before implementing. The skill owns the JSON shape, encoding rules, grid math; this spec is story-level.

Create `NorthPeak Store Operations` dashboard. Save it at the **project root** as `./dashboard.lvdash.json`. Ship datasets **schema-less** (bare table names) so `lakeview create --dataset-catalog/--dataset-schema` inject the target — ONE file works in any catalog/schema. Link the Genie space from section A. (Save the Genie space definition at the project root too — `./genie_space.json`.)

### Why this dashboard works (design principles)

- **Two pages, one story**: page 1 is the glance — *"we're short in the North and over-stocked in the South on the same styles; here's the exposure and where."* Page 2 is the deep-dive — *"which SKUs, which stores, and what the recovery model recommends."*
- **One metric view + two datasets**: `mv_store_position` is the canonical exposure layer (KPI tiles + zone splits — same numbers Genie uses). `gold_store_sku_position` powers every per-position widget (map, shortfall/surplus rollups). `gold_recovery_recommendations` is the third dataset for the recovery-mix + recaptured-$ widget.
- **A map is the visual hook**: full-width store map on page 1 — red northern stockouts, amber southern overstock, on the same SKUs. Instantly readable; beats any table for *"where's the problem?"*.
- **One AI showcase per page**: page 1's map + exposure tiles carry the `ai_classify`-driven markdown signal; page 2 surfaces the **ML recovery recommendation** (recommended-move mix + total predicted recaptured $) — AI-native analytics inside a dashboard.
- **Clean theme — no borders, white canvas**: widgets float on the canvas; left-aligned headers; a cohesive palette where red = stockout/lost-sales and amber = overstock/markdown, so the two symptoms are color-coded consistently everywhere.
- **Self-sufficient pages**: Row 1 of every page is a markdown `text` widget naming the event (what / when / cause / the two symptoms) and telling the reader which widget answers which question. Lift the situation from the README.

### Theme

```
canvasBackgroundColor: #F5F7FB (light) / #0F1419 (dark)
widgetBackgroundColor: #FFFFFF (light) / #161B22 (dark)
widgetBorderColor:     same as widgetBackgroundColor (= no visible border)
fontColor:             #1F2530 (light) / #E8ECF0 (dark)
selectionColor:        #4F7CE3 (light) / #8ACAFF (dark)
visualizationColors:   ["#094074","#3C6997","#5ADBFF","#FFB020","#E5484D"]
widgetHeaderAlignment: LEFT
```

Palette runs cool → warning → alarm: deep navy → steel blue → sky cyan → amber → red. The two warm stops are semantic and pinned everywhere:

**Semantic colors (literal-hex pinned everywhere they appear, NEVER `themeColorType: position N`):**
- **Stockout / lost-sales** → `#E5484D` red (the alarm — northern short positions).
- **Overstock / markdown risk** → `#FFB020` amber (the warning — southern dead-stock).
- **Healthy / everyday** → `#3C6997` steel blue.

**`position_status` color pins (literal-hex on EVERY widget that colors by status)** — Lakeview cycles the palette by result order, which differs across widgets; pinning guarantees `stockout` is the same red on the map AND on the status bars:

| position_status | Hex |
|---|---|
| stockout | `#E5484D` red |
| at_risk | `#FFB020` amber |
| overstock | `#FFB020` amber (shares the warning tone; the map separates them by climate zone) |
| healthy | `#3C6997` steel blue |

### Datasets (3 total)

| Name | Source (schema-less) | Powers |
|---|---|---|
| `ds_exposure` | `SELECT climate_zone, region, category, position_status, MEASURE(\`lost_sales_exposure\`) AS lost_sales_usd, MEASURE(\`markdown_exposure\`) AS markdown_usd, MEASURE(\`stockout_count\`) AS stockout_count, MEASURE(\`overstock_count\`) AS overstock_count, MEASURE(\`position_count\`) AS position_count FROM mv_store_position GROUP BY ALL` | 4 KPI counters + zone/status split bars |
| `ds_positions` | `SELECT store_id, store_name, region, climate_zone, city, store_lat, store_lng, product_id, product_name, category, seasonality, position_status, on_hand_units, recent_units_7d, weeks_of_supply, lost_sales_exposure_usd, markdown_exposure_usd, markdown_risk_score FROM gold_store_sku_position` | Store map, per-SKU status rollups, worst-position tables |
| `ds_recovery` | `SELECT store_id, product_id, recommended_move, recommended_source_store_id, predicted_recaptured_usd, predicted_net_value_usd FROM gold_recovery_recommendations` | Recommended-move mix + total predicted recaptured $ |

**No hardcoded date/zone clamps** — the global filters are the single source of scoping.

### Global filters (left panel — `PAGE_TYPE_GLOBAL_FILTERS`)

| Filter | Column | Datasets | Default |
|---|---|---|---|
| Climate zone | `climate_zone` | ds_exposure, ds_positions | All |
| Region | `region` | ds_exposure, ds_positions | All |
| Category | `category` | ds_exposure, ds_positions | All |
| Position status | `position_status` | ds_exposure, ds_positions | All |

Each filter widget has an explicit `filterTargets[]` binding only the datasets above — **do NOT bind `ds_recovery`** (it's keyed by store×SKU shortfall, not the filter dims; auto-binding on shared column names would drop rows unexpectedly).

### Page 1 — Operations (the glance)

**Row 1** — title markdown. *"NorthPeak Store Operations. Dana Ruiz, SVP Retail Ops. An early cold snap ~3 weeks ago left the same cold-weather styles SOLD OUT in the North (red — lost sales) and DEAD STOCK in the South (amber — markdown clock). This dashboard tracks the exposure and the recovery."*

**Row 2 — 4 × `counter`**. Source: `ds_exposure`. No `period` encoding — each shows the dataset-level sum over the global filter selection.

- **Lost-sales exposure** · `SUM(\`lost_sales_usd\`)` · `number-currency` USD compact · color `#E5484D` red · *the money leaking out of stocked-out stores.*
- **Markdown exposure** · `SUM(\`markdown_usd\`)` · `number-currency` USD compact · color `#FFB020` amber · *the margin at risk in the South.*
- **Stockout positions** · `SUM(\`stockout_count\`)` · number compact · color `#E5484D` red.
- **Overstock positions** · `SUM(\`overstock_count\`)` · number compact · color `#FFB020` amber.

**Row 3 — `symbol-map` · "Store positions on cold-weather apparel"** (full width). Source: `ds_positions` (filter to the 5 affected SKUs at the widget level via `product_id IN (...)`, OR let the global Category=Apparel filter scope it — prefer the SKU filter so the map is unambiguous). Encoding `coordinates: { latitude: store_lat, longitude: store_lng }` (bare field names, NOT `AVG(...)`). Group implicit by `(store_id, city)`, size = `on_hand_units` (or a constant so all stores read equally — pick size = `recent_units_7d` so busy stores are bigger), **color = `position_status`** via the literal-hex pins (red stockout / amber overstock / steel healthy). Tooltip: store_name, city, on_hand, recent_units_7d, position_status. `mark.opacity: 1`.

- *The map is the wow: a red cluster across the North (Denver, Minneapolis, Chicago, Boston…) sitting next to an amber cluster across the South (Phoenix, Miami, Houston…) — same SKUs, opposite problem, one glance. Store 214 (Denver) is a red dot the demo zooms to.*

**Row 4 — two side-by-side**

- **`bar` grouped · "Positions by climate zone & status"** · `ds_exposure` · x = `climate_zone`, y = `SUM(position_count)`, color = `position_status` (literal-hex pins) · *North bar is mostly red (stockout); South bar is mostly amber (overstock); Mixed is mostly steel (healthy) — the split in one chart.*
- **`bar` horizontal · "Exposure by category"** · `ds_exposure` · y = `category`, x = `SUM(lost_sales_usd) + SUM(markdown_usd)` (or two stacked measures) · *Apparel dwarfs Home + General Merchandise — the problem is confined to cold-weather apparel, not the whole catalog.*

### Page 2 — Recovery (the deep-dive)

**Row 1** — title markdown. *"Recovery — what do we do about it? The worst shortfalls, the surplus that can cover them, and the model's recommended move with the revenue it recaptures."*

**Row 2 — worst positions**

- **`table` · "Worst stockouts (lost-sales)"** · `ds_positions` · `WHERE position_status IN ('stockout','at_risk')`, columns store_name, city, product_name, on_hand_units, recent_units_7d, `lost_sales_exposure_usd`, sort exposure DESC · *Store 214 / Summit Down Parka near the top — the demo's spotlight row.*
- **`table` · "Biggest markdown risk (overstock)"** · `ds_positions` · `WHERE position_status='overstock'`, columns store_name, city, product_name, on_hand_units, weeks_of_supply, `markdown_exposure_usd`, sort exposure DESC · *the over-stocked stores — including STORE-0377 (Colorado Springs, ~100 mi from Denver) holding the parkas Store 214 needs.*

**Row 3 — the recovery model**

- **`bar` · "Recommended recovery move (mix)"** · `ds_recovery` · x = `recommended_move` (`transfer`/`expedite`/`substitute`), y = `COUNT(1)` · *transfers dominate where a nearby surplus exists; expedites where it doesn't — the model isn't a fixed rule.*
- **`counter` · "Total predicted recaptured revenue"** · `ds_recovery` · `SUM(\`predicted_recaptured_usd\`)` · `number-currency` USD compact · color `#094074` · *the recoverable slice of the $4.8M lost-sales exposure — the "so what" of acting on the recommendations.*

**Row 4 — `table` · "Recovery recommendations"** (full width) · `ds_recovery` joined to `ds_positions` for names (or a denormalized dataset) · columns store_name, product_name, `recommended_move`, `recommended_source_store_id`, `predicted_recaptured_usd`, `predicted_net_value_usd`, sort net value DESC · *the actionable list the store-ops team works — the app turns each row into an approve-and-transfer action.*

### Validation

Open the published dashboard and confirm the story reads at a glance: the map shows a red North / amber South split on the affected SKUs, the exposure tiles land (~$4.8M lost-sales, ~$5.6M markdown), Store 214 appears in the worst-stockouts table, the recovery-move mix is a plausible blend, and the global filters update every widget. Sanity-check that Genie's "how much are we losing to stockouts?" matches `MEASURE(lost_sales_exposure)` on `mv_store_position`. Add `dashboard_id` to `resources.json`.

---
