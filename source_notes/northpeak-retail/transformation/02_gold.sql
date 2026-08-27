-- =============================================================================
-- NorthPeak Operations — Gold Layer
-- Aggregated analytics tables consumed by dashboard, Genie, metric view, and app
-- =============================================================================

-- -----------------------------------------------------------------------------
-- gold_store_sku_position: THE HEART OF THE DEMO
-- One row per (store, SKU) reflecting the CURRENT position with velocity + status
-- Current snapshot from silver_inventory LEFT JOIN 7-day silver_sales rollup
-- -----------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_store_sku_position
AS
WITH current_inventory AS (
  SELECT *
  FROM silver_inventory
  WHERE snapshot_date = (
    SELECT MAX(snapshot_date) FROM silver_inventory
  )
),
recent_sales AS (
  SELECT
    store_id,
    product_id,
    SUM(units_sold) AS recent_units_7d,
    SUM(net_sales_usd) AS recent_net_sales_7d
  FROM silver_sales
  WHERE sale_date >= DATE_ADD(
    (SELECT MAX(snapshot_date) FROM silver_inventory), -7
  )
  GROUP BY store_id, product_id
)
SELECT
  ci.store_id,
  ci.store_name,
  ci.region,
  ci.climate_zone,
  ci.city,
  ci.store_lat,
  ci.store_lng,
  ci.product_id,
  ci.product_name,
  ci.category,
  ci.subcategory,
  ci.seasonality,
  ci.on_hand_units,
  ci.on_order_units,
  COALESCE(rs.recent_units_7d, 0) AS recent_units_7d,
  COALESCE(rs.recent_net_sales_7d, 0.0) AS recent_net_sales_7d,
  COALESCE(rs.recent_units_7d, 0) / 7.0 AS avg_daily_velocity,
  CASE
    WHEN COALESCE(rs.recent_units_7d, 0) = 0 THEN NULL
    ELSE ci.on_hand_units / (COALESCE(rs.recent_units_7d, 0) / 7.0 * 7)
  END AS weeks_of_supply,
  ci.price_usd,
  ci.markdown_risk_score,
  -- Lost-sales exposure: velocity * price * 30-day horizon (for stocked-out positions)
  CASE
    WHEN ci.on_hand_units = 0 AND COALESCE(rs.recent_units_7d, 0) > 0
    THEN GREATEST(0, (COALESCE(rs.recent_units_7d, 0) / 7.0) * ci.price_usd * 30)
    WHEN ci.on_hand_units > 0 AND COALESCE(rs.recent_units_7d, 0) > 0
      AND ci.on_hand_units / (COALESCE(rs.recent_units_7d, 0) / 7.0 * 7) < 1
    THEN GREATEST(0, ((COALESCE(rs.recent_units_7d, 0) / 7.0) - (ci.on_hand_units / 7.0)) * ci.price_usd * 30)
    ELSE 0
  END AS lost_sales_exposure_usd,
  -- Markdown exposure: surplus units * price * 30% markdown depth (for overstocked)
  CASE
    WHEN ci.on_hand_units > 0
      AND (COALESCE(rs.recent_units_7d, 0) = 0 OR
           ci.on_hand_units / NULLIF(COALESCE(rs.recent_units_7d, 0) / 7.0 * 7, 0) > 8)
      AND ci.markdown_risk_score >= 0.6
    THEN GREATEST(0, ci.on_hand_units * ci.price_usd * 0.3)
    ELSE 0
  END AS markdown_exposure_usd,
  -- Position status: the single column the UI colors by
  CASE
    WHEN ci.on_hand_units = 0 AND COALESCE(rs.recent_units_7d, 0) > 0 THEN 'stockout'
    WHEN COALESCE(rs.recent_units_7d, 0) > 0
      AND ci.on_hand_units / NULLIF(COALESCE(rs.recent_units_7d, 0) / 7.0 * 7, 0) < 1 THEN 'at_risk'
    WHEN (COALESCE(rs.recent_units_7d, 0) = 0 OR
          ci.on_hand_units / NULLIF(COALESCE(rs.recent_units_7d, 0) / 7.0 * 7, 0) > 8)
      AND ci.markdown_risk_score >= 0.6 THEN 'overstock'
    ELSE 'healthy'
  END AS position_status
FROM current_inventory ci
LEFT JOIN recent_sales rs
  ON ci.store_id = rs.store_id AND ci.product_id = rs.product_id;

-- -----------------------------------------------------------------------------
-- gold_open_shortfalls: current stockout/at-risk positions with nearest surplus
-- Enriched with candidate-recovery context for model scoring + app queue
-- -----------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_open_shortfalls
AS
WITH shortfalls AS (
  SELECT *
  FROM gold_store_sku_position
  WHERE position_status IN ('stockout', 'at_risk')
),
surpluses AS (
  SELECT
    store_id AS surplus_store_id,
    product_id,
    region,
    on_hand_units AS surplus_on_hand,
    store_lat,
    store_lng
  FROM gold_store_sku_position
  WHERE position_status = 'overstock'
),
nearest_surplus AS (
  SELECT
    sh.store_id,
    sh.product_id,
    FIRST_VALUE(su.surplus_store_id) OVER (
      PARTITION BY sh.store_id, sh.product_id
      ORDER BY
        -- Prefer same region, then closest
        CASE WHEN su.region = sh.region THEN 0 ELSE 1 END,
        6371 * 2 * ASIN(SQRT(
          POWER(SIN(RADIANS(su.store_lat - sh.store_lat) / 2), 2) +
          COS(RADIANS(sh.store_lat)) * COS(RADIANS(su.store_lat)) *
          POWER(SIN(RADIANS(su.store_lng - sh.store_lng) / 2), 2)
        ))
    ) AS nearest_surplus_store_id,
    FIRST_VALUE(su.surplus_on_hand) OVER (
      PARTITION BY sh.store_id, sh.product_id
      ORDER BY
        CASE WHEN su.region = sh.region THEN 0 ELSE 1 END,
        6371 * 2 * ASIN(SQRT(
          POWER(SIN(RADIANS(su.store_lat - sh.store_lat) / 2), 2) +
          COS(RADIANS(sh.store_lat)) * COS(RADIANS(su.store_lat)) *
          POWER(SIN(RADIANS(su.store_lng - sh.store_lng) / 2), 2)
        ))
    ) AS nearest_surplus_on_hand,
    FIRST_VALUE(
      6371 * 2 * ASIN(SQRT(
        POWER(SIN(RADIANS(su.store_lat - sh.store_lat) / 2), 2) +
        COS(RADIANS(sh.store_lat)) * COS(RADIANS(su.store_lat)) *
        POWER(SIN(RADIANS(su.store_lng - sh.store_lng) / 2), 2)
      ))
    ) OVER (
      PARTITION BY sh.store_id, sh.product_id
      ORDER BY
        CASE WHEN su.region = sh.region THEN 0 ELSE 1 END,
        6371 * 2 * ASIN(SQRT(
          POWER(SIN(RADIANS(su.store_lat - sh.store_lat) / 2), 2) +
          COS(RADIANS(sh.store_lat)) * COS(RADIANS(su.store_lat)) *
          POWER(SIN(RADIANS(su.store_lng - sh.store_lng) / 2), 2)
        ))
    ) AS nearest_surplus_distance_km
  FROM shortfalls sh
  JOIN surpluses su
    ON sh.product_id = su.product_id
    AND su.surplus_store_id != sh.store_id
)
SELECT DISTINCT
  sh.store_id,
  sh.store_name,
  sh.region,
  sh.climate_zone,
  sh.city,
  sh.store_lat,
  sh.store_lng,
  sh.product_id,
  sh.product_name,
  sh.category,
  sh.seasonality,
  sh.on_hand_units,
  sh.avg_daily_velocity,
  sh.lost_sales_exposure_usd,
  sh.price_usd,
  ns.nearest_surplus_store_id,
  ns.nearest_surplus_on_hand,
  ns.nearest_surplus_distance_km
FROM shortfalls sh
LEFT JOIN nearest_surplus ns
  ON sh.store_id = ns.store_id AND sh.product_id = ns.product_id;

-- -----------------------------------------------------------------------------
-- gold_transfer_outcomes: historical recovery moves with situational features
-- Training table for the optional ML model (03-ml-recovery.md)
-- -----------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_transfer_outcomes
AS
SELECT
  transfer_id,
  product_id,
  product_name,
  category,
  move_type,
  from_store_id,
  from_region,
  from_climate,
  to_store_id,
  to_region,
  to_climate,
  substitute_product_id,
  units_moved,
  initiated_date,
  days_to_fulfill,
  distance_km,
  price_usd,
  cost_usd,
  CASE WHEN price_usd > 0 THEN (price_usd - cost_usd) / price_usd ELSE 0 END AS margin_pct,
  CASE WHEN from_region = to_region THEN TRUE ELSE FALSE END AS same_region,
  recaptured_sales_usd,
  margin_impact_usd,
  move_cost_usd
FROM silver_transfers;

-- -----------------------------------------------------------------------------
-- gold_recovery_recommendations: heuristic-ranked recovery move per shortfall
-- For each open shortfall, rank transfer / expedite / substitute by net value
-- Transfer wins for hero shortfall (STORE-0214 x SKU-APP-04412)
-- -----------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_recovery_recommendations
AS
WITH shortfalls AS (
  SELECT
    store_id,
    product_id,
    product_name,
    avg_daily_velocity,
    on_hand_units,
    price_usd,
    nearest_surplus_store_id,
    nearest_surplus_on_hand,
    nearest_surplus_distance_km
  FROM gold_open_shortfalls
),
-- Find a substitute: another cold_weather product in the same subcategory with stock available
substitutes AS (
  SELECT
    sh.store_id,
    sh.product_id,
    FIRST_VALUE(g.product_id) OVER (
      PARTITION BY sh.store_id, sh.product_id
      ORDER BY ABS(g.price_usd - sh.price_usd)
    ) AS substitute_product_id
  FROM shortfalls sh
  JOIN gold_store_sku_position g
    ON g.seasonality = 'cold_weather'
    AND g.product_id != sh.product_id
    AND g.on_hand_units > 0
    AND g.store_id = sh.store_id
),
candidates AS (
  SELECT
    sh.store_id,
    sh.product_id,
    sh.product_name,
    sh.avg_daily_velocity,
    sh.price_usd,
    -- Units needed: velocity * 14 days horizon - current on hand
    GREATEST(1, CAST(sh.avg_daily_velocity * 14 - sh.on_hand_units AS INT)) AS units_needed,
    sh.nearest_surplus_store_id,
    sh.nearest_surplus_on_hand,
    sh.nearest_surplus_distance_km,
    sub.substitute_product_id,

    -- TRANSFER economics
    LEAST(
      GREATEST(1, CAST(sh.avg_daily_velocity * 14 - sh.on_hand_units AS INT)),
      COALESCE(sh.nearest_surplus_on_hand, 0)
    ) AS transfer_units,
    LEAST(
      GREATEST(1, CAST(sh.avg_daily_velocity * 14 - sh.on_hand_units AS INT)),
      COALESCE(sh.nearest_surplus_on_hand, 0)
    ) * sh.price_usd * GREATEST(0.5, 1.0 - COALESCE(sh.nearest_surplus_distance_km, 9999) * 0.0003) AS transfer_recaptured,
    60 + COALESCE(sh.nearest_surplus_distance_km, 9999) * 1.1 AS transfer_cost,

    -- EXPEDITE economics
    GREATEST(1, CAST(sh.avg_daily_velocity * 14 - sh.on_hand_units AS INT)) * sh.price_usd * 0.82 AS expedite_recaptured,
    GREATEST(1, CAST(sh.avg_daily_velocity * 14 - sh.on_hand_units AS INT)) * 9 + 400 AS expedite_cost,

    -- SUBSTITUTE economics
    GREATEST(1, CAST(sh.avg_daily_velocity * 14 - sh.on_hand_units AS INT)) * sh.price_usd * 0.35 AS substitute_recaptured,
    GREATEST(1, CAST(sh.avg_daily_velocity * 14 - sh.on_hand_units AS INT)) * sh.price_usd * 0.58 * 0.45 AS substitute_margin_impact

  FROM shortfalls sh
  LEFT JOIN (
    SELECT DISTINCT store_id, product_id, substitute_product_id
    FROM substitutes
  ) sub
    ON sh.store_id = sub.store_id AND sh.product_id = sub.product_id
),
ranked AS (
  SELECT
    store_id,
    product_id,
    units_needed,
    nearest_surplus_store_id,
    nearest_surplus_on_hand,
    nearest_surplus_distance_km,
    substitute_product_id,
    price_usd,

    -- Net values
    transfer_recaptured - transfer_cost AS transfer_net,
    expedite_recaptured - expedite_cost AS expedite_net,
    substitute_recaptured - substitute_margin_impact AS substitute_net,

    transfer_recaptured,
    expedite_recaptured,
    substitute_recaptured,
    transfer_cost,
    expedite_cost,
    substitute_margin_impact,
    transfer_units
  FROM candidates
)
SELECT
  store_id,
  product_id,
  -- Recommended move = argmax net value
  CASE
    WHEN transfer_net >= expedite_net AND transfer_net >= substitute_net AND nearest_surplus_store_id IS NOT NULL
      THEN 'transfer'
    WHEN expedite_net >= substitute_net
      THEN 'expedite'
    ELSE 'substitute'
  END AS recommended_move,
  CASE
    WHEN transfer_net >= expedite_net AND transfer_net >= substitute_net AND nearest_surplus_store_id IS NOT NULL
      THEN nearest_surplus_store_id
    ELSE NULL
  END AS recommended_source_store_id,
  CASE
    WHEN NOT (transfer_net >= expedite_net AND transfer_net >= substitute_net AND nearest_surplus_store_id IS NOT NULL)
      AND NOT (expedite_net >= substitute_net)
      THEN substitute_product_id
    ELSE NULL
  END AS recommended_substitute_product_id,
  CASE
    WHEN transfer_net >= expedite_net AND transfer_net >= substitute_net AND nearest_surplus_store_id IS NOT NULL
      THEN transfer_units
    ELSE units_needed
  END AS recommended_units,
  CASE
    WHEN transfer_net >= expedite_net AND transfer_net >= substitute_net AND nearest_surplus_store_id IS NOT NULL
      THEN transfer_recaptured
    WHEN expedite_net >= substitute_net
      THEN expedite_recaptured
    ELSE substitute_recaptured
  END AS predicted_recaptured_usd,
  CASE
    WHEN transfer_net >= expedite_net AND transfer_net >= substitute_net AND nearest_surplus_store_id IS NOT NULL
      THEN transfer_net
    WHEN expedite_net >= substitute_net
      THEN expedite_net
    ELSE substitute_net
  END AS predicted_net_value_usd,
  -- move_ranking: JSON array of all three options
  TO_JSON(ARRAY(
    NAMED_STRUCT(
      'move_type', 'transfer',
      'recaptured_usd', transfer_recaptured,
      'net_value_usd', transfer_net,
      'cost_usd', transfer_cost
    ),
    NAMED_STRUCT(
      'move_type', 'expedite',
      'recaptured_usd', expedite_recaptured,
      'net_value_usd', expedite_net,
      'cost_usd', expedite_cost
    ),
    NAMED_STRUCT(
      'move_type', 'substitute',
      'recaptured_usd', substitute_recaptured,
      'net_value_usd', substitute_net,
      'cost_usd', substitute_margin_impact
    )
  )) AS move_ranking,
  CURRENT_TIMESTAMP() AS scored_at
FROM ranked;
