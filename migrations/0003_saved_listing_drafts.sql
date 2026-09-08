CREATE TABLE listing_drafts (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    draft JSONB NOT NULL,
    ebay_category JSONB,
    ebay_condition JSONB,
    photo_count SMALLINT NOT NULL CHECK (photo_count BETWEEN 1 AND 24),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX listing_drafts_user_updated_idx
    ON listing_drafts (user_id, updated_at DESC);

ALTER TABLE ebay_listings
    ADD COLUMN listing_draft_id UUID UNIQUE
    REFERENCES listing_drafts(id) ON DELETE SET NULL;
