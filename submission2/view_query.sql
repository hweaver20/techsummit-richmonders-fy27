-- NorthPeak Store Ops: Live Shortfall View
-- Ranked by lost sales exposure (revenue at risk)
-- Reads from Lakebase synced tables (northpeak schema) + writable actions (northpeak_app schema)
WITH latest_snap AS (
    SELECT MAX(snapshot_date) AS max_date FROM northpeak.synced_inventory
),
velocity AS (
    SELECT store_id, product_id, AVG(on_hand_units) AS avg_daily_drawdown
    FROM northpeak.synced_inventory
    WHERE snapshot_date >= (SELECT max_date - INTERVAL '7 days' FROM latest_snap)
    GROUP BY store_id, product_id
),
shortfalls AS (
    SELECT i.store_id, i.store_name, i.city, i.region,
        i.product_id, i.product_name, i.on_hand_units, i.on_order_units, i.price_usd,
        CASE WHEN i.on_hand_units = 0 THEN i.price_usd * COALESCE(v.avg_daily_drawdown, 5) * 14
             ELSE i.price_usd * GREATEST(COALESCE(v.avg_daily_drawdown, 5) * 7 - i.on_hand_units, 0)
        END AS lost_sales_exposure_usd,
        CASE WHEN COALESCE(v.avg_daily_drawdown, 1) > 0
             THEN i.on_hand_units / GREATEST(v.avg_daily_drawdown, 0.1) ELSE 999 END AS days_of_cover,
        CASE WHEN i.on_hand_units = 0 THEN 'stockout'
             WHEN i.on_hand_units < COALESCE(v.avg_daily_drawdown, 5) * 3 THEN 'at_risk'
             ELSE 'healthy' END AS position_status
    FROM northpeak.synced_inventory i
    CROSS JOIN latest_snap ls
    LEFT JOIN velocity v ON v.store_id = i.store_id AND v.product_id = i.product_id
    WHERE i.snapshot_date = ls.max_date AND i.climate_zone = 'North' AND i.seasonality = 'cold_weather'
),
recovery_status AS (
    SELECT destination_store_id AS store_id, product_id, status AS recovery_status
    FROM northpeak_app.transfer_actions
    WHERE status IN ('proposed','approved','committed')
)
SELECT s.store_id, s.store_name, s.city, s.region, s.product_id, s.product_name,
    s.on_hand_units, s.on_order_units,
    ROUND(s.lost_sales_exposure_usd::numeric, 0) AS lost_sales_exposure_usd,
    ROUND(s.days_of_cover::numeric, 1) AS days_of_cover,
    s.position_status, COALESCE(r.recovery_status, 'none') AS recovery_status
FROM shortfalls s
LEFT JOIN recovery_status r ON r.store_id = s.store_id AND r.product_id = s.product_id
WHERE s.position_status IN ('stockout', 'at_risk')
ORDER BY s.lost_sales_exposure_usd DESC
LIMIT 50;
