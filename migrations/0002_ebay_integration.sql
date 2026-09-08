CREATE TABLE ebay_connections (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    ebay_user_id TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    encrypted_refresh_token TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('connected', 'expired')),
    connected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ebay_listings (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    request_id UUID NOT NULL,
    custom_label TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('ready', 'seller_hub_transfer', 'live', 'ended')
    ),
    draft JSONB NOT NULL,
    ebay_item_id TEXT,
    revision TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, request_id),
    UNIQUE (user_id, custom_label),
    UNIQUE (ebay_item_id)
);

CREATE TABLE ebay_media (
    id BIGSERIAL PRIMARY KEY,
    listing_id UUID NOT NULL REFERENCES ebay_listings(id) ON DELETE CASCADE,
    position SMALLINT NOT NULL CHECK (position BETWEEN 1 AND 24),
    image_id TEXT NOT NULL,
    image_url TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    UNIQUE (listing_id, position)
);

CREATE TABLE ebay_feed_transfers (
    id UUID PRIMARY KEY,
    listing_id UUID NOT NULL UNIQUE REFERENCES ebay_listings(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    safe_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
