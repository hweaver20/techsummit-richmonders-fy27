# Lakeflow — Data Ingestion + Processing

## Shared Context (referenced by all other spec files)

**Affected SKUs** (deterministic — the cold-snap cluster; must exist with these exact values). All are cold-weather apparel, so their demand moves together when the weather turns:

| product_id | product_name | category | subcategory | price_usd | cost_usd |
|------------|--------------|----------|-------------|-----------|----------|
| SKU-APP-04412 | Summit Down Parka | Apparel | Outerwear | 249.00 | 96.00 |
| SKU-APP-04418 | Ridgeline Insulated Jacket | Apparel | Outerwear | 189.00 | 74.00 |
| SKU-APP-04431 | Timberline Fleece Hoodie | Apparel | Tops | 79.00 | 24.00 |
| SKU-APP-04455 | Alpine Wool Beanie | Apparel | Accessories | 34.00 | 9.00 |
| SKU-APP-04460 | Frostguard Thermal Gloves | Apparel | Accessories | 44.00 | 12.00 |

`SKU-APP-04412` (**Summit Down Parka**) is the **hero SKU** — the one the demo spotlights on Store 214.

**Hero store**: `STORE-0214` — Denver, CO, climate zone `North`. The demo's spotlight shortfall. Deterministic. Its surplus counterpart the model recommends transferring from is `STORE-0377` — **Colorado Springs, CO (~100 mi / ~160 km south of Denver, same `West` region)**. It carries climate zone `Mixed` but is an **over-allocated** store: it got a normal-plan cold-weather allocation, and because the cold snap concentrated demand in the Denver metro its stock hasn't moved — so it's the nearby surplus the transfer draws from. (Over-stock here is an inventory posture, not a pure climate tag.)

**The anomaly (one misallocation, two visible symptoms)**: an early cold snap ~3 weeks ago shifted cold-weather-apparel demand toward cold-climate (North) stores faster than the nightly replenishment plan could react. On the **same 5 SKUs**:
- **Stockout side (North)** — ~30 northern stores at **0 on-hand** with **rising recent sell-through** → real demand walking out unfilled (lost sales, shown RED).
- **Markdown side (South)** — ~40 southern stores holding **high on-hand** on those SKUs with **near-zero recent sell-through** → dead-stock the markdown clock is running on (shown AMBER).

This is the load-bearing shape: **same SKU, opposite problem, one map**. The recovery move ("transfer from a nearby surplus store") is literally true in the data because the surplus stores physically hold the units the stocked-out stores need.

**Markdown-risk notes** (verbatim merchandising-note phrases, used predominantly on over-stocked southern positions — feed the note pool in Section A so `ai_classify` has a clear signal). Aging/dead-stock tone: *"season ending, still full racks"*, *"no movement in three weeks"*, *"warm-weather store, cold gear not selling"*, *"overstocked vs plan"*, *"clearance candidate"*. Healthy tone (for non-affected positions): *"selling to plan"*, *"steady turns"*. These must be exact substrings — Genie + the dashboard search for them.

**Time references**: `NOW = datetime.now()` by default (rolling — the dashboard's right edge is always yesterday-real; set `NORTHPEAK_PIN_TIME=1` to freeze `NOW` for recorded videos / baked-in IDs). `HISTORY_START = NOW − 18 months` (sell-through + transfer history for the model). `SNAP_ONSET = NOW − 21 days` (~3 weeks back — the cold snap begins). `VELOCITY_RAMP = NOW − 18 days` (northern sell-through on affected SKUs climbs). `SNAPSHOT_DATE = NOW − 1 day` (the "current" inventory snapshot the app + dashboard read). **Causal chain**: normal-plan allocation before −3w → cold snap at −3w → northern sell-through ramps −3w to −1w, draining on-hand to 0 by ~−1w → southern positions untouched, aging → the CURRENT snapshot (yesterday) shows both symptoms at once. Peak of the velocity divergence sits in the past week-and-a-half, clearly to the left of the chart edge.

> Numbers in this file are demo targets, not invariants — match the narrative shape, don't sweat ±10%. Parallelization rules live in `SKILL.md` → **Parallelization with Subagents**.

---

## A. Synthetic Data Generation

**Skill**: `databricks-synthetic-data-gen` (read `SKILLS/databricks-synthetic-data-gen/SKILL.md`). Use the pre-provisioned databricks-connect venv (Python 3.12 + faker + numpy + pandas + holidays + pyarrow) — system prompt has the path; do NOT create a new venv. Generation is **pure Spark** — `spark.range` + `F.when` + broadcast joins + Window functions + `F.element_at` against literal arrays. No driver loops, no `.collect()` on big tables.

Write the raw datasets as **parquet files into the UC Volume** `/Volumes/{catalog}/{schema}/raw_data/<dataset>/` (one subdir per dataset, named without the `raw_` prefix). This Volume is the raw landing zone; SDP silver reads it via `read_files()` — no bronze pass-through, no raw Delta tables:

| Table | Rows | Notes |
|-------|------|-------|
| `raw_stores` | 400 | US stores. Climate zone: North ~35%, South ~35%, Mixed ~30%. City anchors + GPS on every store (drives the Operations map). ~5 regions (Northeast/Southeast/Midwest/West/South-Central). |
| `raw_products` | ~2,000 | Catalog sample (the "40K SKUs" is talking-track; generate ~2K so joins stay cheap). Apparel ~45% ($20–260), Home ~30% ($15–400), General Merchandise ~25% ($5–120). The 5 affected SKUs sit at fixed positions in Apparel/Outerwear/Tops/Accessories. `seasonality` tag (`cold_weather`/`warm_weather`/`all_season`) — the 5 affected SKUs are `cold_weather`. Each product carries a short searchable **`description`** (material/feature/category text) — the 3 insulated outerwear/mid-layer affected SKUs describe as mutual warm-layer substitutes. This text is what **Lakebase Search** (Milestone 2) indexes and what the app's product search + the **substitute** recovery move query ("find a comparable in-stock product"). |
| `raw_sales` | ~3.5M | Daily store×SKU POS sell-through, 18 months. One row per (store, SKU, sale_date) with `units_sold`, `net_sales_usd`. Sparse — only store×SKU pairs that sold that day. The cold-snap velocity shift lives here (Section: The Event). |
| `raw_inventory_snapshots` | ~255K | End-of-day on-hand for the affected positions across the last ~14 days + a current-snapshot (`SNAPSHOT_DATE`) sample of everyday positions. `on_hand_units`, `on_order_units`. The affected SKUs show North→0 / South→surplus on the current snapshot. |
| `raw_transfers` | ~40K | Historical inter-store transfers + expedites + substitutions over 18 months, each with an OUTCOME (`recaptured_sales_usd`, `days_to_fulfill`, `margin_impact_usd`) — the **training data for the recovery model** (`03-ml-recovery.md`). ~3 move types: `transfer`, `expedite`, `substitute`. |
| `raw_store_traffic` | ~220K | Daily foot-traffic by store, 18 months (`traffic_count`) — context for velocity + a talking-track "why demand is real" signal. |

### Data Variation

Sell-through seasonality (on `raw_sales`) — the load-bearing shape is the **cold-weather-apparel velocity divergence**, but everyday sales need realistic rhythm so the anomaly stands out, not drowns:

- **Weekly rhythm** — weekend (Sat/Sun) ~1.6× weekday; apply ±15% daily gaussian noise, clip to a floor of 0.05 so no day disappears.
- **Baseline seasonality** — cold-weather SKUs sell steadily-low in warm months, ramping into fall everywhere; warm-weather SKUs the inverse; all-season flat. Keep it gentle so the cold-snap divergence dominates.
- **Black Friday tent** Nov 24–30 across all categories (~2.2×) — a real spike so the chart isn't monotone, but placed so it doesn't collide with the cold-snap window (if `SNAP_ONSET` lands within BF week, the anomaly still reads because it's store-and-SKU-specific, not a total-sales spike).

**The regional demand split (the whole story):** cold-weather-apparel sell-through is **climate-driven**, not uniform. Northern stores sell 3–5× the cold-weather units of southern stores in a normal fall week; the cold snap pushes northern velocity higher still while southern stays flat. This single rule produces the North-red / South-amber map without forcing it.

### Note pool (`merch_note_text` on inventory positions)

~15 hand-coded strings in 2 tones — keeps synth deterministic and gives `ai_classify` a clear signal. **Aging/dead-stock** (must include the Shared-Context markdown-risk phrases verbatim): assertive "this isn't moving" tone, attached predominantly to over-stocked southern positions on the affected SKUs. **Healthy**: "selling to plan", "steady turns" — everyday positions. **Distribution** (the classifier's signal): over-stocked southern affected positions → 85% aging / 15% healthy · everyday positions → 10% aging / 90% healthy.

### Store master + GPS

Each store gets `store_lat` + `store_lng` (DOUBLE PRECISION) = city anchor + ~0.02° jitter so points spread. **Required for the story**: cluster ~30 affected northern stores across cold cities (Denver, Minneapolis, Chicago, Boston, Buffalo, Salt Lake City…) with `STORE-0214` = Denver at a fixed position; ~40 over-stocked stores across warm cities (Phoenix, Miami, Houston, San Diego, Tampa…). The hero surplus `STORE-0377` = **Colorado Springs** at a fixed position (~100 mi from Denver, same `West` region) so the recommended transfer is genuinely "a short drive" — it sits in the over-stocked set despite its `Mixed` climate tag. Lat/lng to 2 decimals is enough. `position_status` (derived in gold), not the raw climate tag, drives the map color.

### The Event

The cold snap is a **store×SKU velocity + on-hand divergence**, not a total-sales spike:

- **Northern affected stores** (~30) on the 5 cold-weather SKUs: sell-through ramps from a normal fall baseline starting `VELOCITY_RAMP` (~2.5 weeks ago), climbing ~2.5–3× over ~10 days. On-hand drains correspondingly: `raw_inventory_snapshots` shows these positions decaying to **0 on_hand** by ~1 week ago and staying at 0 through the current snapshot, while `on_order` lags (replenishment can't keep up). Recent 7-day units_sold stays high (demand is real; the zeros are lost sales, not lost interest).
- **Southern over-stocked stores** (~40) on the same 5 SKUs: normal-plan allocation left them well-stocked, but warm-climate sell-through is near-zero. `raw_inventory_snapshots` shows **high on_hand** (weeks of supply) with 7-day units_sold ≈ 0. `merch_note_text` on these positions is predominantly aging-toned.
- **Everything else** (all-season + warm SKUs, mixed-climate stores) behaves normally — the divergence is confined to the affected SKUs so the anomaly is legible.

Quantify the exposure so the KPIs land: annualized lost-sales on the stocked-out northern positions ≈ **$4.8M** (recent velocity × unfilled days × price); markdown exposure on the southern surplus ≈ **$5.6M** (surplus units × price × expected markdown depth). These are demo targets — the generation should produce data that rolls up roughly to them.

**Transfer/expedite/substitute history (`raw_transfers`) — the model's training signal.** Over the 18-month history, generate realistic recovery moves with outcomes so the model in `03-ml-recovery.md` can learn which move recaptures the most revenue in which situation:
- `transfer` (store→store): cheapest, moderate speed; recaptures high sales when a nearby same-region surplus store exists; `margin_impact_usd` small (just freight).
- `expedite` (DC→store): faster but higher cost; recaptures high sales but eats margin.
- `substitute` (offer a comparable in-stock SKU): instant, but leaks the customer to a (often lower-margin) item — lower `recaptured_sales_usd`, sometimes negative `margin_impact_usd`.
- Make the outcomes **learnable**: transfers from a close, same-region surplus store with plenty of units should show the best `recaptured_sales_usd` per dollar; expedites win when no nearby surplus exists; substitutes win when the shortfall is small and time-critical. This is what lets the model rank `STORE-0214`'s Summit Down Parka shortfall as **transfer from STORE-0377** — because history says so.

### Raw table schemas (gen output)

ID formats: `STORE-NNNN` / `SKU-XXX-NNNNN` / `TRF-NNNNNNNN`. PKs in **bold**, FKs marked. Tables prefix with `raw_` (no bronze).

- **`raw_stores`** — **store_id**, store_name, region (`Northeast/Southeast/Midwest/West/South-Central`), climate_zone (`North/South/Mixed`), city, state, `store_lat`/`store_lng` (DOUBLE, city anchor + jitter), open_date, square_feet, format (`flagship/standard/express`).
- **`raw_products`** — **product_id**, product_name, category (`Apparel/Home/General Merchandise`), subcategory, price_usd, cost_usd, seasonality (`cold_weather/warm_weather/all_season`), **description** (STRING — short searchable blurb; the text Lakebase Search + the substitute-recovery lookup match on), launch_date, is_active.
- **`raw_sales`** — store_id (FK), product_id (FK), sale_date (DATE), units_sold (INT), net_sales_usd (DOUBLE), channel (`in_store/bopis/ship_from_store`). One row per (store, SKU, day) that had a sale.
- **`raw_inventory_snapshots`** — store_id (FK), product_id (FK), snapshot_date (DATE), on_hand_units (INT), on_order_units (INT), merch_note_text (STRING, nullable — populated on affected + a sample of everyday positions). Daily for the last ~14 days + `SNAPSHOT_DATE`.
- **`raw_transfers`** — **transfer_id**, product_id (FK), from_store_id (FK, nullable for expedite/DC), to_store_id (FK), move_type (`transfer/expedite/substitute`), substitute_product_id (FK, nullable), units_moved (INT), initiated_date (DATE), days_to_fulfill (INT), recaptured_sales_usd (DOUBLE), margin_impact_usd (DOUBLE), cost_usd (DOUBLE). 18-month history — the recovery model's labeled outcomes.
- **`raw_store_traffic`** — store_id (FK), traffic_date (DATE), traffic_count (INT). Daily foot traffic.

---

## B. SDP Pipeline

**Skill to use**: `databricks-pipelines` — read `SKILLS/databricks-pipelines/SKILL.md` before implementing.

Create pipeline `northpeak_operations` transforming raw parquet → analytics tables. Configure with a `configuration: {catalog, schema}` block and read the Volume via `read_files('/Volumes/${catalog}/${schema}/raw_data/...')` so it works on any target catalog/schema.

### Consumer Requirements

| Consumer | Needs | From Table |
|----------|-------|------------|
| Dashboard KPIs (lost-sales $, markdown $, sell-through) + trend | daily/store/SKU exposure metrics by climate zone + category | `mv_store_position` metric view (over `gold_store_sku_position`, defined in `02-uc-governance.md`) |
| Dashboard map + shortfall/surplus widgets | per store×SKU current position with geo + climate + on_hand + recent velocity + status flag | `gold_store_sku_position` (widget-level GROUP BY for region/category rollups) |
| Genie "where are we short/over-stocked and why" | same per-position fact with denormalized store + product + note | `gold_store_sku_position` |
| Recovery model training (`03-ml-recovery.md`) | one row per historical move with situational features + outcome label | `gold_transfer_outcomes` |
| Recovery model scoring input | one row per OPEN shortfall (stocked-out North position) + candidate-surplus context | `gold_open_shortfalls` |
| App's Operations queue (open shortfalls + ranked recovery) | current shortfalls with store/SKU/geo + ranked move + predicted recaptured $ | `gold_open_shortfalls` JOIN `gold_recovery_recommendations` (built by the pipeline heuristic; ML optional — `03-ml-recovery.md`) |
| App's analytics drill-downs (Delta via warehouse) | sell-through trend, worst positions, per-region rollups | `silver_sales`, `gold_store_sku_position` |

### Raw layer (no bronze pass-through)

The data-gen step in Section A writes 6 raw parquet datasets into the `raw_data` Volume: `stores`, `products`, `sales`, `inventory_snapshots`, `transfers`, `store_traffic`. SDP silver reads these files via `read_files()` — there is no bronze layer (the gen's output is already typed and clean).

### Raw → Silver (joins + expectations + `ai_classify` dedup MV)

Four silver materialized views — three facts (`silver_sales`, `silver_inventory`, `silver_transfers`) plus one small dedup helper (`note_markdown_flags`).

**`note_markdown_flags`** — *the `ai_classify` showcase, sized down*. The synth uses a canned pool of ~15 distinct `merch_note_text` strings across hundreds of thousands of inventory rows. Running `ai_classify` per-row would issue that many LLM calls; instead build a small MV over `SELECT DISTINCT merch_note_text` and call `ai_classify` once per distinct string:

```sql
SELECT merch_note_text,
  CASE ai_classify(merch_note_text,
        ARRAY('dead_stock','aging','healthy'))
    WHEN 'dead_stock' THEN 1.0
    WHEN 'aging'      THEN 0.6
    ELSE 0.1
  END AS markdown_risk_score
FROM (SELECT DISTINCT merch_note_text FROM raw_inventory_snapshots
      WHERE merch_note_text IS NOT NULL)
```

`silver_inventory` joins back on `merch_note_text` so every position inherits the score without a second LLM call. Talking-track: *"one built-in SQL function turns a merchandiser's free-text note into a markdown-risk signal — no UDF, no separate service, and it scales because we dedup."*

**`silver_sales`** — per store×SKU×day denormalized fact. `raw_sales` JOIN `raw_stores` (→ region, climate_zone, city, geo) JOIN `raw_products` (→ product_name, category, subcategory, price_usd, cost_usd, seasonality). Columns: `store_id`, `store_name`, `region`, `climate_zone`, `city`, `store_lat`, `store_lng`, `product_id`, `product_name`, `category`, `subcategory`, `seasonality`, `sale_date` (DATE), `units_sold`, `net_sales_usd`, `channel`. Cluster by `sale_date`.

**`silver_inventory`** — current + recent on-hand position, denormalized. `raw_inventory_snapshots` JOIN `raw_stores` JOIN `raw_products` JOIN `note_markdown_flags` (→ markdown_risk_score). Columns: store/product denormalized dims (as above), `snapshot_date` (DATE), `on_hand_units`, `on_order_units`, `merch_note_text`, **`markdown_risk_score`** (COALESCE → 0.1 on no match). Cluster by `snapshot_date`.

**`silver_transfers`** — recovery-move history, denormalized. `raw_transfers` JOIN `raw_products` (→ product_name, category, price/cost) JOIN `raw_stores` twice (from + to → region, climate, geo). Columns: `transfer_id`, `product_id`, `product_name`, `category`, `move_type`, `from_store_id`, `from_region`, `from_climate`, `to_store_id`, `to_region`, `to_climate`, `substitute_product_id`, `units_moved`, `initiated_date` (DATE), `days_to_fulfill`, `recaptured_sales_usd`, `margin_impact_usd`, `cost_usd`, plus a derived `distance_km` (haversine from store coords — a model feature). Powers the recovery-model training table.

### Silver → Gold (aggregations)

**Dashboard-filter contract.** Every aggregate consumed by the dashboard MUST carry `climate_zone`, `region`, and `category` as filter dimensions. `gold_store_sku_position` enforces this directly; any future gold MV must follow the same rule or global filters silently stop applying.

**`gold_store_sku_position`** — *the heart of the demo* — one row per (store, SKU) reflecting the CURRENT position (`snapshot_date = SNAPSHOT_DATE`) with recent velocity and a status flag. Built from `silver_inventory` (current snapshot) LEFT JOIN a 7-day `silver_sales` rollup (`recent_units_7d`, `recent_net_sales_7d`) on (store, SKU). Dims: `store_id`, `store_name`, `region`, `climate_zone`, `city`, `store_lat`, `store_lng`, `product_id`, `product_name`, `category`, `subcategory`, `seasonality`. Metrics/fields: `on_hand_units`, `on_order_units`, `recent_units_7d`, `recent_net_sales_7d`, `avg_daily_velocity` (recent_units_7d / 7), `weeks_of_supply` (on_hand / NULLIF(avg_daily_velocity×7, 0)), `price_usd`, `markdown_risk_score`, and two derived $ measures + a status flag:
- `lost_sales_exposure_usd` — for stocked-out/short positions: `GREATEST(0, avg_daily_velocity × price_usd × unfilled_days_horizon)` (demo horizon ~30 days) — the revenue leaking while on_hand can't meet velocity.
- `markdown_exposure_usd` — for over-stocked positions: `GREATEST(0, (on_hand_units − expected_sellthrough) × price_usd × markdown_depth)` where `markdown_depth` ≈ 0.3 — the margin at risk of being discounted away.
- **`position_status`** (the single column the UI colors by): `'stockout'` (on_hand=0 AND avg_daily_velocity>0), `'at_risk'` (weeks_of_supply < 1 AND velocity>0), `'overstock'` (weeks_of_supply > 8 AND markdown_risk_score ≥ 0.6), `'healthy'` (else). The affected northern positions → `stockout`, the southern surplus → `overstock`.

> `gold_store_sku_position` is what the dashboard map, the metric view, Genie, and the app's Operations view all read. It is the coherence spine — every downstream "where are we short/over-stocked" answer resolves here.

**`gold_open_shortfalls`** — the current stockout/at-risk positions the app + model act on. `gold_store_sku_position WHERE position_status IN ('stockout','at_risk')`, enriched with candidate-recovery context: for each shortfall, the nearest same-region `overstock` position on the SAME SKU (`nearest_surplus_store_id`, `nearest_surplus_on_hand`, `nearest_surplus_distance_km`) and the DC lead-time constant. Columns: shortfall store/SKU/geo + `on_hand_units`, `avg_daily_velocity`, `lost_sales_exposure_usd`, `nearest_surplus_store_id`, `nearest_surplus_on_hand`, `nearest_surplus_distance_km`. This is BOTH the model's scoring input (`03-ml-recovery.md`) and, joined to the model's output, the app's Operations queue.

**`gold_transfer_outcomes`** — recovery-move history, one row per historical move. Pass-through from `silver_transfers` + situational features derived at move time: `move_type`, `units_moved`, `distance_km`, `from_on_hand_at_move` / `to_velocity_at_move` (reconstructed from `silver_inventory`/`silver_sales` around `initiated_date`), `days_to_fulfill`, `price_usd`, `margin_pct`, and the OUTCOME `recaptured_sales_usd` + `margin_impact_usd`. Two uses: (a) the heuristic below can derive its rate coefficients from it (`AVG(...) GROUP BY move_type`); (b) it's the training table if a team takes the OPTIONAL ML path (`03-ml-recovery.md`).

**`gold_recovery_recommendations`** — *the ranked recovery move per open shortfall* — **built by the pipeline with a hardcoded HEURISTIC** (no ML needed; ML is an optional swap, see `03-ml-recovery.md`). For each row in `gold_open_shortfalls`, construct the three candidate moves and rank by **net value = recaptured − cost − margin_impact**, computed in SQL from the candidate economics:
- **transfer** from `nearest_surplus_store_id`: `recaptured ≈ recommended_units × price_usd × 0.9` (degrade slightly with `nearest_surplus_distance_km`); `cost ≈ 60 + distance_km × 1.1`; `margin_impact ≈ 0`. `recommended_units = LEAST(units_needed, nearest_surplus_on_hand)`.
- **expedite** from DC: `recaptured ≈ units_needed × price_usd × 0.82`; heavier `cost ≈ units_needed × 9 + 400`; `margin_impact ≈ 0`; slower.
- **substitute**: pick the substitute SKU by a simple in-pipeline rule — **another `cold_weather` product in the same `subcategory` with on-hand available somewhere and the closest `price_usd`** (`recommended_substitute_product_id`); `recaptured ≈ units_needed × price_usd × 0.35` (demand leaks); `margin_impact ≈ units_needed × unit_margin × 0.45` where `unit_margin ≈ price_usd × 0.58` (catalog cost ≈ 42% of price — join `raw_products.cost_usd` for the exact figure, or use the 0.58 rate). This makes substitute the worst net-value play, as intended.
- `net_value = recaptured − cost − margin_impact`; `recommended_move` = argmax net_value; `move_ranking` = a JSON array of all three moves with their `recaptured`/`net`/`cost`. Columns match the schema in `03-ml-recovery.md` → Inference shape (`store_id`, `product_id`, `recommended_move`, `recommended_source_store_id`, `recommended_substitute_product_id`, `recommended_units`, `predicted_recaptured_usd`, `predicted_net_value_usd`, `move_ranking`, `scored_at`). The coefficients mirror the outcomes in `gold_transfer_outcomes`, so **transfer wins for the hero shortfall** (`STORE-0214 × SKU-APP-04412` → transfer from `STORE-0377`).

### Consumer routing

- `mv_store_position` (over `gold_store_sku_position`) → dashboard KPIs + Genie headline answers. Same definitions everywhere (`02-uc-governance.md`).
- `gold_store_sku_position` → dashboard map + shortfall/surplus widgets via widget-level `GROUP BY`.
- `gold_open_shortfalls` → recovery-model scoring input AND (joined with the model output) the app's Operations queue.
- `gold_recovery_recommendations` → the app's Operations queue (ranked recovery move per shortfall) + the dashboard's recovery widgets. Built by the pipeline heuristic; optionally overwritten by the ML path.
- `gold_transfer_outcomes` → the heuristic's coefficient source AND the training table for the OPTIONAL ML path (`03-ml-recovery.md`). Dashboard + app read `gold_recovery_recommendations`, not the raw outcomes.
- `silver_sales` → app analytics drill-downs (sell-through trend) via warehouse SQL.

---

## C. Validation

Run before `03-ml-recovery.md`. Each row = a one-line query the LLM writes against the table; if it fails, fix the synth before publishing downstream resources.

**Load-bearing (must pass — these gate the story):**
- **The hero shortfall exists** — `gold_store_sku_position WHERE store_id='STORE-0214' AND product_id='SKU-APP-04412'` → `on_hand_units = 0`, `avg_daily_velocity > 0`, `position_status = 'stockout'`, `lost_sales_exposure_usd > 0`.
- **The recommended surplus exists** — `gold_open_shortfalls WHERE store_id='STORE-0214' AND product_id='SKU-APP-04412'` → `nearest_surplus_store_id = 'STORE-0377'` (Colorado Springs, same `West` region, ~100 mi away) with `nearest_surplus_on_hand` comfortably ≥ the shortfall need. The transfer story must be true in the data.
- **North-red / South-amber split** — `gold_store_sku_position WHERE product_id IN (<5 affected SKUs>)` GROUP BY `climate_zone`, `position_status`: North rows are overwhelmingly `stockout`/`at_risk`, South rows overwhelmingly `overstock`. ~30 northern stockout stores, ~40 southern overstock stores.
- **Anomaly confined to affected SKUs** — non-affected SKUs are overwhelmingly `healthy`; the divergence doesn't bleed everywhere (or the map is noise).
- **Exposure KPIs land** — `SUM(lost_sales_exposure_usd)` on affected stockouts ≈ $4.8M; `SUM(markdown_exposure_usd)` on affected overstock ≈ $5.6M (±20% OK).
- **`markdown_risk_score` separates** — `AVG(markdown_risk_score)` on southern affected overstock positions ≥ 0.6; on healthy positions ≤ 0.2.
- **`note_markdown_flags` dedup is doing its job** — `COUNT(DISTINCT merch_note_text) << COUNT(*)` on `raw_inventory_snapshots`; MV row count matches the distinct count.
- **Transfer outcomes are learnable** — `gold_transfer_outcomes` GROUP BY `move_type`: `transfer` moves from close, same-region, high-surplus sources show the best `recaptured_sales_usd` per `cost_usd`; `expedite` wins when `nearest_surplus_distance_km` is large; `substitute` shows lower recaptured $ and thinner margin. If the three move types don't separate on outcome, the model can't rank them — regenerate.
- **Velocity ramp is in the past** — weekly `SUM(units_sold)` on affected SKUs in northern stores shows a build starting ~2.5w ago, not a cliff at the current week.

**Smoke checks** (the LLM derives these — verify upstream invariants didn't break): `climate_zone` enum is `{North, South, Mixed}`; store GPS non-null and in earth-bounds (lat [-90,90], lng [-180,180]); `position_status` enum is the 4 values above; `gold_open_shortfalls` has a few dozen rows (not zero, not thousands); `weeks_of_supply` never negative.

Add `pipeline_id` to `resources.json`.
