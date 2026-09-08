ALTER TABLE ebay_listings
    ADD COLUMN listing_draft_revision BIGINT;

ALTER TABLE listing_drafts
    ADD COLUMN revision BIGINT NOT NULL DEFAULT 1;

ALTER TABLE ebay_listings
    DROP CONSTRAINT ebay_listings_state_check;

ALTER TABLE ebay_listings
    ADD CONSTRAINT ebay_listings_state_check CHECK (
        state IN ('ready', 'publishing', 'seller_hub_transfer', 'live', 'ended')
    );
