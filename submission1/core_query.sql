-- Which stores are short on a top-selling product right now?
-- Run against synced data in Lakebase (production branch)
SELECT i.store_id, s.store_name, s.city, s.state,
       i.product_id, i.product_name,
       i.on_hand_units, i.on_order_units
FROM northpeak.synced_inventory i
JOIN northpeak.synced_stores s ON i.store_id = s.store_id
WHERE i.product_id = 'SKU-APP-04412'  -- Summit Down Parka (top seller)
  AND i.snapshot_date = '2026-08-26'
  AND i.on_hand_units = 0
ORDER BY i.store_id;
