# UC Governance — Metric View

Tables defined in `01-lakeflow.md`. Skill: `databricks-metric-views`.

## Metric View — `mv_store_position`

Source: `gold_store_sku_position` (the current per store×SKU position). Single view, aggregated materialization. This is the **one governed definition** of NorthPeak's exposure metrics — the dashboard KPI tiles, Dana's Genie answers, and the app all read these same measures, so the numbers match wherever she looks.

**Dimensions**: `climate_zone`, `region`, `category`, `position_status`, `product_id`, `product_name`.

**Measures** (full list — referenced verbatim by dashboard datasets + Genie example SQLs + the app's KPI tiles, so any rename here is a breaking change downstream):

| Name | Expression |
|------|------------|
| `lost_sales_exposure` | `SUM(lost_sales_exposure_usd)` |
| `markdown_exposure` | `SUM(markdown_exposure_usd)` |
| `on_hand_units` | `SUM(on_hand_units)` |
| `recent_units_7d` | `SUM(recent_units_7d)` |
| `recent_net_sales_7d` | `SUM(recent_net_sales_7d)` |
| `position_count` | `COUNT(1)` |
| `stockout_count` | `SUM(CASE WHEN position_status = 'stockout' THEN 1 ELSE 0 END)` |
| `overstock_count` | `SUM(CASE WHEN position_status = 'overstock' THEN 1 ELSE 0 END)` |
| `avg_weeks_of_supply` | `AVG(weeks_of_supply)` |
| `avg_markdown_risk` | `AVG(markdown_risk_score)` |

Count/flag measures use `SUM(CASE WHEN … )` (not `MEASURE(x)/MEASURE(y)`) so the engine computes them at the filtered-slice level — correct under any global dashboard filter and safe on empty slices. `avg_weeks_of_supply` is an average of a per-row derived field; it's a coarse health signal, not a KPI tile (the two exposure $ measures + the two counts are the tiles).

**Materialization**: aggregated on `(climate_zone, region, category, position_status, product_id) × all measures`, refresh every 6h. (The position table is a daily snapshot, so 6h refresh comfortably covers it.)

### Consumers

- **Dashboard KPI tiles** — Lost-sales exposure ($), Markdown exposure ($), Stockout positions (#), Overstock positions (#) — all via `MEASURE(...)`.
- **Genie headline answers** — "how much are we losing to stockouts?", "what's our markdown exposure?", "how many positions are stocked out in the North?" resolve to these measures. Per-widget bindings live in `04-ai-bi.md`.
- **The app's KPI cards** — the Operations page reads the same measures (via warehouse SQL over the MV) so the app header matches the dashboard exactly.

> The recovery model (`03-ml-recovery.md`) does **not** consume `mv_store_position`. It trains on `gold_transfer_outcomes` (per-move history) and scores `gold_open_shortfalls` (per-shortfall) — different grain. `mv_store_position` is the aggregated exposure layer; do not unify.

### Validation

- `MEASURE(lost_sales_exposure)` filtered to `climate_zone='North'` + the 5 affected SKUs ≈ $4.8M; `MEASURE(markdown_exposure)` filtered to `climate_zone='South'` + affected SKUs ≈ $5.6M.
- `MEASURE(stockout_count)` filtered to affected SKUs + North ≈ 30; `MEASURE(overstock_count)` filtered to affected SKUs + South ≈ 40.
- Genie's answer to "how much are we losing to stockouts on cold-weather apparel?" matches `MEASURE(lost_sales_exposure)` for that slice exactly.
- `DESCRIBE EXTENDED` shows the aggregated materialization on the declared dimension set.

Add `metric_view_name` to `resources.json`.
