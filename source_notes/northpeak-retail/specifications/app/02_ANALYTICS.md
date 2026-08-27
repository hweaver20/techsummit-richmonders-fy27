# Analytics Page

Light, bespoke charts over Delta (via SQL Warehouse) — secondary to the embedded AI/BI dashboard, useful for one or two drill-downs tied to the story. Reads the Gold tables the SDP pipeline wrote (`01-lakeflow.md`), NOT Lakebase.

## Charts (2–4, aligned to the story's key numbers)

Rewrite/replace every file in `config/queries/` for this domain (the template ships LuxeBeauty examples that point at nothing). Update `client/src/analytics/AnalyticsView.tsx` so its `queryKey` list matches the files kept. Suggested set:

- **`cold_weather_velocity_trend.sql`** — daily/weekly `SUM(units_sold)` on the 5 affected SKUs, split by `climate_zone` (North vs South), last ~8 weeks, from `silver_sales`. *The line that tells the cold-snap story: northern velocity ramps ~3 weeks ago while southern stays flat — the divergence, in one chart.*
- **`worst_shortfalls.sql`** — top open shortfalls by `lost_sales_exposure_usd` from `gold_store_sku_position WHERE position_status IN ('stockout','at_risk')`: store_name, city, product_name, on_hand, recent velocity, exposure $. *Store 214 / Summit Down Parka near the top.*
- **`position_mix_by_zone.sql`** — position count by `climate_zone` × `position_status` from `gold_store_sku_position` on the affected SKUs. *North = mostly stockout, South = mostly overstock — the split as a grouped bar.*
- **`recovery_recommendations.sql`** *(optional)* — the model's recommended-move mix + `SUM(predicted_recaptured_usd)` from `gold_recovery_recommendations`. *What the recovery model recommends across the portfolio + the recoverable revenue.*

Each `.sql` uses bare/`${catalog}.${schema}` table names that the app resolves to the demo's catalog + schema at boot (the template's placeholder `FROM` clauses point at nothing — replace them, or `/analytics` logs `TABLE_OR_VIEW_NOT_FOUND`).

## Store drill-down (optional)

A small panel: pick a climate zone → list its worst shortfalls → click a shortfall → navigate to `/operations?store=<store_id>&sku=<product_id>` (the Operations queue reads the query params and filters). Mirrors the template's facility drill-down, rekeyed to stores.
