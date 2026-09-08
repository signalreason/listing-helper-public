-- Shared public taxonomy lists, isolated by environment, US tree, and group root.
CREATE TABLE ebay_category_lists (
    cache_key TEXT PRIMARY KEY,
    categories JSONB NOT NULL CHECK (jsonb_typeof(categories) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
