from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta

import pytest

from app.ebay_store import (
    DraftChangedDuringPublication,
    DraftPublicationInProgress,
    DraftRevisionConflict,
    MemoryEbayStore,
    PostgresEbayStore,
    StoredListing,
    StoredMedia,
    StoredTransfer,
)


class FakeCursor(AbstractContextManager):
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.parameters: list[tuple[object, ...]] = []
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, query: str, parameters):
        self.queries.append(" ".join(query.split()))
        self.parameters.append(parameters)
        if query.lstrip().startswith("SELECT 1 FROM listing_drafts"):
            self.result = {"exists": 1}
        elif query.lstrip().startswith("SELECT state, ebay_item_id"):
            self.result = None
        elif "ANY(%s::uuid[])" in query:
            self.result = []
        else:
            self.result = {
                "id": "draft-id",
                "user_id": 1,
                "draft": {},
                "ebay_category": None,
                "ebay_condition": None,
                "photo_count": 1,
                "updated_at": datetime.now(UTC),
                "revision": 1,
            }
        return self

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result


class FakeConnection(AbstractContextManager):
    def __init__(self, cursor: FakeCursor) -> None:
        self.test_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self, **_kwargs):
        return self.test_cursor

    def execute(self, query: str, parameters):
        return self.test_cursor.execute(query, parameters)


class PublicationCursor(FakeCursor):
    publication_lease = datetime.now(UTC)

    def execute(self, query: str, parameters):
        normalized = " ".join(query.split())
        self.queries.append(normalized)
        self.parameters.append(parameters)
        if normalized.startswith("SELECT listing_draft_id::text"):
            self.result = {
                "listing_draft_id": "draft-id",
                "listing_draft_revision": 2,
                "preparation_id": "preparation-id",
            }
        elif normalized.startswith("SELECT revision FROM listing_drafts"):
            self.result = {"revision": 2}
        elif normalized.startswith("SELECT state, listing_draft_id::text"):
            self.result = {
                "state": "ready",
                "listing_draft_id": "draft-id",
                "listing_draft_revision": 2,
                "preparation_id": "preparation-id",
                "publication_started_at": None,
                "publication_attempted": False,
            }
        elif normalized.startswith("UPDATE ebay_listings SET state = 'publishing'"):
            self.result = {"publication_started_at": self.publication_lease}
        else:
            self.result = None
        return self


class PublishingDeleteCursor(FakeCursor):
    def execute(self, query: str, parameters):
        normalized = " ".join(query.split())
        self.queries.append(normalized)
        self.parameters.append(parameters)
        if normalized.startswith("SELECT 1 FROM listing_drafts"):
            self.result = {"exists": 1}
        elif normalized.startswith("SELECT id::text, state FROM ebay_listings"):
            self.result = {"id": "listing-id", "state": "publishing"}
        else:
            self.result = None
        return self


class ListingRevisionCursor(FakeCursor):
    def __init__(self, state: str = "ready") -> None:
        super().__init__()
        self.state = state

    def execute(self, query: str, parameters):
        normalized = " ".join(query.split())
        self.queries.append(normalized)
        self.parameters.append(parameters)
        if normalized.startswith("SELECT revision FROM listing_drafts"):
            self.result = {"revision": 2}
        elif normalized.startswith("SELECT id::text, user_id, request_id::text"):
            self.result = {
                "id": "draft-id",
                "user_id": 1,
                "request_id": "draft-id",
                "custom_label": "label",
                "state": self.state,
                "draft": {},
                "ebay_item_id": "item-id" if self.state == "live" else None,
                "revision": None,
                "listing_draft_id": "draft-id",
                "listing_draft_revision": 1,
                "publication_started_at": None,
                "publication_attempted": False,
            }
        elif normalized.startswith("INSERT INTO ebay_listings"):
            self.result = {
                "id": "draft-id",
                "user_id": 1,
                "request_id": "draft-id",
                "custom_label": "label",
                "state": "ready",
                "draft": {},
                "ebay_item_id": None,
                "revision": None,
                "listing_draft_id": "draft-id",
                "listing_draft_revision": 2,
                "publication_started_at": None,
                "publication_attempted": False,
            }
        else:
            self.result = None
        return self


class MediaRevisionCursor(FakeCursor):
    def __init__(
        self,
        *,
        state: str = "ready",
        started_at: datetime | None = None,
        preparation_id: str = "current-preparation",
    ) -> None:
        super().__init__()
        self.state = state
        self.started_at = started_at
        self.preparation_id = preparation_id

    def execute(self, query: str, parameters):
        normalized = " ".join(query.split())
        self.queries.append(normalized)
        self.parameters.append(parameters)
        if normalized.startswith("SELECT state, listing_draft_revision"):
            self.result = {
                "state": self.state,
                "listing_draft_revision": 2,
                "preparation_id": self.preparation_id,
                "publication_started_at": self.started_at,
            }
        else:
            self.result = None
        return self


def test_postgres_draft_save_locks_source_and_listing_before_update(monkeypatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: connection)

    saved = store.save_draft(1, "draft-id", {}, None, None, 1)

    assert saved is not None
    assert cursor.queries[0].startswith("SELECT revision FROM listing_drafts")
    assert cursor.queries[0].endswith("FOR UPDATE")
    assert cursor.queries[1].startswith("SELECT state, ebay_item_id FROM ebay_listings")
    assert cursor.queries[1].endswith("FOR UPDATE")
    assert cursor.queries[2].startswith("INSERT INTO listing_drafts")


def test_draft_save_rejects_a_stale_revision() -> None:
    store = MemoryEbayStore()
    first = store.save_draft(1, "draft-id", {"version": 1}, None, None, 1)
    assert first is not None
    second = store.save_draft(1, "draft-id", {"version": 2}, None, None, 1, first.revision, True)
    assert second is not None

    with pytest.raises(DraftRevisionConflict):
        store.save_draft(1, "draft-id", {"version": 3}, None, None, 1, first.revision, True)

    assert store.get_draft(1, "draft-id") == second


def test_postgres_draft_save_rejects_a_stale_revision(monkeypatch) -> None:
    cursor = FakeCursor()
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection(cursor))

    with pytest.raises(DraftRevisionConflict):
        store.save_draft(1, "draft-id", {"version": 2}, None, None, 1, 2, True)

    assert len(cursor.queries) == 1


def test_postgres_draft_listing_batch_casts_ids_to_uuid_array(monkeypatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: connection)
    draft_ids = ["11111111-1111-1111-1111-111111111111"]

    listings = store.get_draft_listings(1, draft_ids)

    assert listings == {}
    assert "id = ANY(%s::uuid[])" in cursor.queries[0]
    assert cursor.parameters[0] == (1, draft_ids)


def test_postgres_publication_locks_source_before_listing(monkeypatch) -> None:
    cursor = PublicationCursor()
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection(cursor))

    publication_lease = store.begin_listing_publication(
        "listing-id", expected_preparation_id="preparation-id"
    )

    assert cursor.queries[1].startswith("SELECT revision FROM listing_drafts")
    assert cursor.queries[1].endswith("FOR UPDATE")
    assert cursor.queries[2].startswith("SELECT state, listing_draft_id::text")
    assert cursor.queries[2].endswith("FOR UPDATE")
    assert cursor.queries[3].startswith("UPDATE ebay_listings SET state = 'publishing'")
    assert publication_lease == cursor.publication_lease


def test_postgres_records_an_add_attempt_before_publication(monkeypatch) -> None:
    cursor = FakeCursor()
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection(cursor))

    publication_lease = datetime.now(UTC)

    store.mark_listing_publication_attempted("listing-id", publication_lease)

    assert cursor.queries[0].startswith("UPDATE ebay_listings SET publication_attempted = TRUE")
    assert "state = 'publishing'" in cursor.queries[0]
    assert "publication_started_at = %s" in cursor.queries[0]
    assert cursor.parameters[0] == ("listing-id", publication_lease)


def test_postgres_publication_abort_is_fenced_to_its_lease(monkeypatch) -> None:
    cursor = FakeCursor()
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection(cursor))
    publication_lease = datetime.now(UTC)

    store.abort_listing_publication("listing-id", publication_lease)

    assert "publication_started_at = %s" in cursor.queries[0]
    assert cursor.parameters[0] == ("listing-id", publication_lease)


def test_postgres_rejects_draft_deletion_during_publication(monkeypatch) -> None:
    cursor = PublishingDeleteCursor()
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection(cursor))

    with pytest.raises(DraftPublicationInProgress):
        store.delete_draft(1, "draft-id")

    assert cursor.queries[0].endswith("FOR UPDATE")
    assert cursor.queries[1].endswith("FOR UPDATE")
    assert not any(query.startswith("DELETE") for query in cursor.queries)


def test_postgres_discards_media_when_source_revision_changes(monkeypatch) -> None:
    cursor = ListingRevisionCursor("seller_hub_transfer")
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection(cursor))

    listing = store.get_or_create_listing(
        1,
        "draft-id",
        "label",
        {},
        "draft-id",
        2,
    )

    assert listing.listing_draft_revision == 2
    media_delete = next(
        index
        for index, query in enumerate(cursor.queries)
        if query.startswith("DELETE FROM ebay_media")
    )
    listing_update = next(
        index
        for index, query in enumerate(cursor.queries)
        if query.startswith("INSERT INTO ebay_listings")
    )
    assert media_delete < listing_update
    assert cursor.parameters[media_delete] == ("draft-id",)
    assert "draft = EXCLUDED.draft" in cursor.queries[listing_update]


def test_postgres_media_write_rejects_an_older_preparation_revision(monkeypatch) -> None:
    cursor = MediaRevisionCursor()
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection(cursor))

    with pytest.raises(DraftChangedDuringPublication):
        store.save_media(
            StoredMedia("listing-id", 1, "old", "https://img.test/old.jpg", None),
            1,
        )

    assert cursor.queries[0].endswith("FOR UPDATE")
    assert not any(query.startswith("INSERT INTO ebay_media") for query in cursor.queries)


def test_postgres_media_write_rejects_a_superseded_preparation(monkeypatch) -> None:
    cursor = MediaRevisionCursor()
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection(cursor))

    with pytest.raises(DraftChangedDuringPublication):
        store.save_media(
            StoredMedia("listing-id", 1, "old", "https://img.test/old.jpg", None),
            2,
            "older-preparation",
        )

    assert not any(query.startswith("INSERT INTO ebay_media") for query in cursor.queries)


def test_postgres_media_write_allows_a_matching_stale_recovery(monkeypatch) -> None:
    cursor = MediaRevisionCursor(
        state="publishing", started_at=datetime.now(UTC) - timedelta(minutes=16)
    )
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection(cursor))

    store.save_media(
        StoredMedia("listing-id", 1, "fresh", "https://img.test/fresh.jpg", None),
        2,
    )

    assert cursor.queries[1].startswith("INSERT INTO ebay_media")


def test_listing_creation_rejects_a_changed_source_draft() -> None:
    store = MemoryEbayStore()
    first = store.save_draft(1, "draft-id", {}, None, None, 1)
    assert first is not None
    changed = store.save_draft(1, "draft-id", {"title": "changed"}, None, None, 1)
    assert changed is not None

    with pytest.raises(DraftChangedDuringPublication):
        store.get_or_create_listing(
            1,
            "draft-id",
            "label",
            {},
            "draft-id",
            first.revision,
        )


def test_listing_preparation_updates_snapshot_and_revision_together() -> None:
    store = MemoryEbayStore()
    first = store.save_draft(1, "draft-id", {"version": 1}, None, None, 1)
    assert first is not None
    store.get_or_create_listing(1, "draft-id", "label", {"version": 1}, "draft-id", first.revision)
    second = store.save_draft(1, "draft-id", {"version": 2}, None, None, 1)
    assert second is not None

    prepared = store.get_or_create_listing(
        1, "draft-id", "label", {"version": 2}, "draft-id", second.revision
    )

    assert prepared.draft == {"version": 2}
    assert prepared.listing_draft_revision == second.revision


def test_listing_preparation_rejects_a_published_listing() -> None:
    store = MemoryEbayStore()
    draft = store.save_draft(1, "draft-id", {"version": 1}, None, None, 1)
    assert draft is not None
    listing = store.get_or_create_listing(
        1, "draft-id", "label", {"version": 1}, "draft-id", draft.revision
    )
    store.mark_listing_live(listing.id, "item-id", "listing-revision")

    with pytest.raises(DraftChangedDuringPublication):
        store.get_or_create_listing(
            1, "draft-id", "label", {"version": 2}, "draft-id", draft.revision
        )

    assert store.get_listing(1, listing.id).draft == {"version": 1}


def test_postgres_listing_preparation_rejects_a_published_listing(monkeypatch) -> None:
    cursor = ListingRevisionCursor("live")
    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection(cursor))

    with pytest.raises(DraftChangedDuringPublication):
        store.get_or_create_listing(1, "draft-id", "label", {}, "draft-id", 2)

    assert not any(query.startswith("INSERT INTO ebay_listings") for query in cursor.queries)


def test_legacy_transfer_revision_change_discards_old_media() -> None:
    store = MemoryEbayStore()
    first = store.save_draft(1, "draft-id", {"version": 1}, None, None, 1)
    assert first is not None
    listing = store.get_or_create_listing(
        1, "draft-id", "label", {"version": 1}, "draft-id", first.revision
    )
    store.save_media(StoredMedia(listing.id, 1, "old", "https://img.test/old.jpg", None))
    store.save_transfer(
        StoredTransfer(
            "transfer-id",
            listing.id,
            1,
            "task-id",
            "FAILED",
            None,
            datetime.now(UTC),
        )
    )
    second = store.save_draft(1, "draft-id", {"version": 2}, None, None, 1)
    assert second is not None

    prepared = store.get_or_create_listing(
        1, "draft-id", "label", {"version": 2}, "draft-id", second.revision
    )

    assert prepared.state == "seller_hub_transfer"
    assert prepared.listing_draft_revision == second.revision
    assert store.list_media(listing.id) == []


def test_media_write_rejects_an_older_preparation_revision() -> None:
    store = MemoryEbayStore()
    first = store.save_draft(1, "draft-id", {"version": 1}, None, None, 1)
    assert first is not None
    listing = store.get_or_create_listing(
        1, "draft-id", "label", {"version": 1}, "draft-id", first.revision
    )
    second = store.save_draft(1, "draft-id", {"version": 2}, None, None, 1)
    assert second is not None
    store.get_or_create_listing(1, "draft-id", "label", {"version": 2}, "draft-id", second.revision)

    with pytest.raises(DraftChangedDuringPublication):
        store.save_media(
            StoredMedia(listing.id, 1, "old", "https://img.test/old.jpg", None),
            first.revision,
        )


def test_publication_state_blocks_source_draft_changes() -> None:
    store = MemoryEbayStore()
    source = store.save_draft(1, "draft-id", {}, None, None, 1)
    assert source is not None
    listing = store.get_or_create_listing(
        1,
        "draft-id",
        "label",
        {},
        "draft-id",
        source.revision,
    )

    publication_lease = store.begin_listing_publication(listing.id)

    with pytest.raises(DraftPublicationInProgress):
        store.save_draft(1, "draft-id", {"title": "changed"}, None, None, 1)
    with pytest.raises(DraftPublicationInProgress):
        store.update_listing(listing.id, {"title": "changed"}, None)
    with pytest.raises(DraftPublicationInProgress):
        store.save_media(StoredMedia(listing.id, 1, "image", "https://img.test/1.jpg", None))

    store.mark_listing_live(listing.id, "item-id", "revision", publication_lease=publication_lease)
    assert store.get_listing(1, listing.id).state == "live"


def test_publication_start_rejects_a_source_change_after_preparation() -> None:
    store = MemoryEbayStore()
    source = store.save_draft(1, "draft-id", {}, None, None, 1)
    assert source is not None
    listing = store.get_or_create_listing(
        1,
        "draft-id",
        "label",
        {},
        "draft-id",
        source.revision,
    )
    assert store.save_draft(1, "draft-id", {"title": "changed"}, None, None, 1)

    with pytest.raises(DraftChangedDuringPublication):
        store.begin_listing_publication(listing.id)


def test_publication_start_rejects_a_newer_preparation_than_the_loaded_snapshot() -> None:
    store = MemoryEbayStore()
    first = store.save_draft(1, "draft-id", {}, None, None, 1)
    assert first is not None
    listing = store.get_or_create_listing(
        1, "draft-id", "label", {"version": 1}, "draft-id", first.revision
    )
    second = store.save_draft(1, "draft-id", {"version": 2}, None, None, 1)
    assert second is not None
    store.get_or_create_listing(1, "draft-id", "label", {"version": 2}, "draft-id", second.revision)

    with pytest.raises(DraftChangedDuringPublication):
        store.begin_listing_publication(listing.id, first.revision)


def test_publication_start_rejects_a_superseded_same_revision_preparation() -> None:
    store = MemoryEbayStore()
    source = store.save_draft(1, "draft-id", {}, None, None, 1)
    assert source is not None
    listing = store.get_or_create_listing(
        1, "draft-id", "label", {"policy": "first"}, "draft-id", source.revision, "first"
    )
    store.get_or_create_listing(
        1, "draft-id", "label", {"policy": "second"}, "draft-id", source.revision, "second"
    )

    with pytest.raises(DraftChangedDuringPublication):
        store.begin_listing_publication(listing.id, source.revision, "first")
    with pytest.raises(DraftChangedDuringPublication):
        store.save_media(
            StoredMedia(listing.id, 1, "old", "https://img.test/old.jpg", None),
            source.revision,
            "first",
        )


def test_stale_publication_can_resume_with_the_same_preparation() -> None:
    store = MemoryEbayStore()
    source = store.save_draft(1, "draft-id", {}, None, None, 1)
    assert source is not None
    listing = store.get_or_create_listing(
        1, "draft-id", "label", {}, "draft-id", source.revision, "first"
    )
    publication_lease = store.begin_listing_publication(listing.id)
    store.mark_listing_publication_attempted(listing.id, publication_lease)
    publishing = store.get_listing(1, listing.id)
    assert publishing is not None
    assert publishing.publication_attempted is True
    with pytest.raises(DraftPublicationInProgress):
        store.get_or_create_listing(
            1, "draft-id", "label", {"changed": "must not apply"}, "draft-id", source.revision
        )
    store.listings[listing.id] = type(publishing)(
        **{
            **publishing.__dict__,
            "publication_started_at": datetime.now(UTC) - timedelta(minutes=16),
        }
    )

    with pytest.raises(DraftChangedDuringPublication):
        store.get_or_create_listing(
            1,
            "draft-id",
            "label",
            {"changed": "must not apply"},
            "draft-id",
            source.revision,
            "second",
        )
    recovered = store.get_or_create_listing(
        1,
        "draft-id",
        "label",
        {"changed": "must not apply"},
        "draft-id",
        source.revision,
        "first",
    )
    store.begin_listing_publication(recovered.id)

    resumed = store.get_listing(1, listing.id)
    assert resumed is not None
    assert resumed.state == "publishing"
    assert resumed.draft == {}
    assert resumed.publication_attempted is True
    assert resumed.publication_started_at > datetime.now(UTC) - timedelta(minutes=1)


def test_renewed_publication_lease_rejects_old_cleanup_and_completion() -> None:
    store = MemoryEbayStore()
    source = store.save_draft(1, "draft-id", {}, None, None, 1)
    assert source is not None
    listing = store.get_or_create_listing(1, "draft-id", "label", {}, "draft-id", source.revision)
    store.begin_listing_publication(listing.id)
    publishing = store.get_listing(1, listing.id)
    assert publishing is not None
    old_lease = datetime.now(UTC) - timedelta(minutes=16)
    store.listings[listing.id] = StoredListing(
        **{**publishing.__dict__, "publication_started_at": old_lease}
    )
    renewed_lease = store.begin_listing_publication(listing.id)

    with pytest.raises(DraftPublicationInProgress):
        store.mark_listing_publication_attempted(listing.id, old_lease)
    store.abort_listing_publication(listing.id, old_lease)
    with pytest.raises(DraftPublicationInProgress):
        store.mark_listing_live(listing.id, "old-item", None, publication_lease=old_lease)

    renewed = store.get_listing(1, listing.id)
    assert renewed is not None
    assert renewed.state == "publishing"
    assert renewed.publication_started_at == renewed_lease
    store.mark_listing_live(listing.id, "new-item", None, publication_lease=renewed_lease)
    assert store.get_listing(1, listing.id).ebay_item_id == "new-item"
