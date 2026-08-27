-- Query against synced UC table (tech_summit_richmonders.northpeak.silver_inventory_dedup)
-- Running in Lakebase Postgres via northpeak.synced_inventory
SELECT i.store_id, s.store_name, s.city, s.state,
       i.product_id, i.product_name,
       i.on_hand_units, i.on_order_units, i.snapshot_date
FROM northpeak.synced_inventory i
JOIN northpeak.synced_stores s ON i.store_id = s.store_id
WHERE i.store_id = 'STORE-0214'
  AND i.product_id = 'SKU-APP-04412'
  AND i.snapshot_date = '2026-08-26';
