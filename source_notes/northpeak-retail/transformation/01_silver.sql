-- =============================================================================
-- NorthPeak Operations — Silver Layer
-- Denormalized facts from raw parquet (Volume) → analytics-ready materialized views
-- =============================================================================

-- -----------------------------------------------------------------------------
-- note_markdown_flags: ai_classify dedup MV
-- One LLM call per DISTINCT merch_note_text (not per row) → markdown_risk_score
-- -----------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW note_markdown_flags
AS
SELECT
  merch_note_text,
  CASE ai_classify(merch_note_text, ARRAY('dead_stock', 'aging', 'healthy'))
    WHEN 'dead_stock' THEN 1.0
    WHEN 'aging'      THEN 0.6
    ELSE 0.1
  END AS markdown_risk_score
FROM (
  SELECT DISTINCT merch_note_text
  FROM read_files('/Volumes/${catalog}/${schema}/raw_data/inventory_snapshots/')
  WHERE merch_note_text IS NOT NULL
);

-- -----------------------------------------------------------------------------
-- silver_sales: per store×SKU×day denormalized fact
-- raw_sales JOIN raw_stores (region, climate, geo) JOIN raw_products (name, category, seasonality)
-- -----------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW silver_sales
AS
SELECT
  s.store_id,
  st.store_name,
  st.region,
  st.climate_zone,
  st.city,
  st.store_lat,
  st.store_lng,
  s.product_id,
  p.product_name,
  p.category,
  p.subcategory,
  p.seasonality,
  s.sale_date,
  s.units_sold,
  s.net_sales_usd,
  s.channel
FROM read_files('/Volumes/${catalog}/${schema}/raw_data/sales/') s
JOIN read_files('/Volumes/${catalog}/${schema}/raw_data/stores/') st
  ON s.store_id = st.store_id
JOIN read_files('/Volumes/${catalog}/${schema}/raw_data/products/') p
  ON s.product_id = p.product_id;

-- -----------------------------------------------------------------------------
-- silver_inventory: current + recent on-hand, denormalized with markdown risk
-- raw_inventory_snapshots JOIN raw_stores JOIN raw_products JOIN note_markdown_flags
-- -----------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW silver_inventory
AS
SELECT
  i.store_id,
  st.store_name,
  st.region,
  st.climate_zone,
  st.city,
  st.store_lat,
  st.store_lng,
  i.product_id,
  p.product_name,
  p.category,
  p.subcategory,
  p.seasonality,
  p.price_usd,
  p.cost_usd,
  i.snapshot_date,
  i.on_hand_units,
  i.on_order_units,
  i.merch_note_text,
  COALESCE(n.markdown_risk_score, 0.1) AS markdown_risk_score
FROM read_files('/Volumes/${catalog}/${schema}/raw_data/inventory_snapshots/') i
JOIN read_files('/Volumes/${catalog}/${schema}/raw_data/stores/') st
  ON i.store_id = st.store_id
JOIN read_files('/Volumes/${catalog}/${schema}/raw_data/products/') p
  ON i.product_id = p.product_id
LEFT JOIN note_markdown_flags n
  ON i.merch_note_text = n.merch_note_text;

-- -----------------------------------------------------------------------------
-- silver_transfers: recovery-move history, denormalized with haversine distance
-- raw_transfers JOIN raw_products JOIN raw_stores (from + to)
-- -----------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW silver_transfers
AS
SELECT
  t.transfer_id,
  t.product_id,
  p.product_name,
  p.category,
  p.price_usd,
  p.cost_usd,
  t.move_type,
  t.from_store_id,
  sf.region AS from_region,
  sf.climate_zone AS from_climate,
  sf.store_lat AS from_lat,
  sf.store_lng AS from_lng,
  t.to_store_id,
  st2.region AS to_region,
  st2.climate_zone AS to_climate,
  st2.store_lat AS to_lat,
  st2.store_lng AS to_lng,
  t.substitute_product_id,
  t.units_moved,
  t.initiated_date,
  t.days_to_fulfill,
  t.recaptured_sales_usd,
  t.margin_impact_usd,
  t.cost_usd AS move_cost_usd,
  -- Haversine distance in km (model feature)
  CASE
    WHEN t.from_store_id IS NOT NULL THEN
      6371 * 2 * ASIN(SQRT(
        POWER(SIN(RADIANS(st2.store_lat - sf.store_lat) / 2), 2) +
        COS(RADIANS(sf.store_lat)) * COS(RADIANS(st2.store_lat)) *
        POWER(SIN(RADIANS(st2.store_lng - sf.store_lng) / 2), 2)
      ))
    ELSE 0
  END AS distance_km
FROM read_files('/Volumes/${catalog}/${schema}/raw_data/transfers/') t
JOIN read_files('/Volumes/${catalog}/${schema}/raw_data/products/') p
  ON t.product_id = p.product_id
LEFT JOIN read_files('/Volumes/${catalog}/${schema}/raw_data/stores/') sf
  ON t.from_store_id = sf.store_id
JOIN read_files('/Volumes/${catalog}/${schema}/raw_data/stores/') st2
  ON t.to_store_id = st2.store_id;
