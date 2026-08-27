-- Agent Change: product_substitutes table
-- Created on dev branch, validated, promoted to production
-- Co-authored-by: Genie Code <genie-code@databricks.com>
-- Co-authored-by: travis.lawrence@databricks.com

CREATE TABLE northpeak_app.product_substitutes (
    substitute_id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    product_id TEXT NOT NULL,
    substitute_product_id TEXT NOT NULL,
    substitution_type TEXT NOT NULL CHECK (substitution_type IN ('equivalent', 'upgrade', 'downgrade', 'similar')),
    confidence_score NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    reason TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(product_id, substitute_product_id)
);

-- Seed data: substitutes for the hero SKU (Summit Down Parka)
INSERT INTO northpeak_app.product_substitutes (product_id, substitute_product_id, substitution_type, confidence_score, reason)
VALUES
    ('SKU-APP-04412', 'SKU-APP-04418', 'similar', 0.85, 'Ridgeline Insulated Jacket is a lighter-weight alternative to Summit Down Parka'),
    ('SKU-APP-04412', 'SKU-APP-04431', 'downgrade', 0.60, 'Timberline Fleece Hoodie for milder cold when parka unavailable'),
    ('SKU-APP-04418', 'SKU-APP-04412', 'upgrade', 0.85, 'Summit Down Parka is a heavier-weight upgrade to Ridgeline Insulated Jacket');
