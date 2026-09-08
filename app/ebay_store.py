"""Durable eBay connection, listing, media, and transfer records."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import psycopg
from psycopg.rows import dict_row


class EbayStoreUnavailable(Exception):
    def __init__(
        self,
        message: str = "eBay data store is unavailable",
        *,
        error_class: str = "database",
        sqlstate: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.sqlstate = sqlstate


def _store_unavailable(error: psycopg.Error) -> EbayStoreUnavailable:
    if isinstance(error, psycopg.OperationalError):
        error_class = "connection"
    elif isinstance(error, (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedTable)):
        error_class = "schema"
    elif isinstance(error, psycopg.IntegrityError):
        error_class = "constraint"
    else:
        error_class = "database"
    return EbayStoreUnavailable(error_class=error_class, sqlstate=error.sqlstate)


class EbayAccountAlreadyConnected(Exception):
    pass


class DraftChangedDuringPublication(Exception):
    pass


class DraftPublicationInProgress(Exception):
    pass


class DraftRevisionConflict(Exception):
    pass


PUBLICATION_LEASE_TIME = timedelta(minutes=15)


@dataclass(frozen=True)
class EbayConnection:
    user_id: int
    ebay_user_id: str
    display_name: str
    encrypted_refresh_token: str
    status: str
    connected_at: datetime


@dataclass(frozen=True)
class StoredListing:
    id: str
    user_id: int
    request_id: str
    custom_label: str
    state: str
    draft: dict[str, object]
    ebay_item_id: str | None
    revision: str | None
    listing_draft_id: str | None = None
    listing_draft_revision: int | None = None
    preparation_id: str | None = None
    publication_started_at: datetime | None = None
    publication_attempted: bool = False


@dataclass(frozen=True)
class StoredDraft:
    id: str
    user_id: int
    draft: dict[str, object]
    ebay_category: dict[str, object] | None
    ebay_condition: dict[str, object] | None
    photo_count: int
    updated_at: datetime
    revision: int = 1


@dataclass(frozen=True)
class StoredMedia:
    listing_id: str
    position: int
    image_id: str
    image_url: str
    expires_at: datetime | None


@dataclass(frozen=True)
class StoredTransfer:
    id: str
    listing_id: str
    user_id: int
    task_id: str
    status: str
    error: str | None
    updated_at: datetime


class EbayStore(Protocol):
    def get_category_list(self, key: str) -> list[dict[str, str]] | None: ...

    def save_category_list(self, key: str, categories: list[dict[str, str]]) -> None: ...

    def healthcheck(self) -> None: ...

    def get_connection(self, user_id: int) -> EbayConnection | None: ...

    def connect(self, connection: EbayConnection) -> None: ...

    def disconnect(self, user_id: int) -> None: ...

    def mark_authorization_expired(self, user_id: int) -> None: ...

    def delete_ebay_user_data(self, ebay_user_id: str) -> None: ...

    def save_draft(
        self,
        user_id: int,
        draft_id: str,
        draft: dict[str, object],
        ebay_category: dict[str, object] | None,
        ebay_condition: dict[str, object] | None,
        photo_count: int,
        expected_revision: int | None = None,
        enforce_revision: bool = False,
    ) -> StoredDraft | None: ...

    def get_draft(self, user_id: int, draft_id: str) -> StoredDraft | None: ...

    def list_drafts(self, user_id: int) -> list[StoredDraft]: ...

    def get_draft_listings(
        self, user_id: int, draft_ids: list[str]
    ) -> dict[str, StoredListing]: ...

    def delete_draft(self, user_id: int, draft_id: str) -> bool: ...

    def get_or_create_listing(
        self,
        user_id: int,
        request_id: str,
        custom_label: str,
        draft: dict[str, object],
        listing_draft_id: str | None = None,
        expected_draft_revision: int | None = None,
        preparation_id: str | None = None,
    ) -> StoredListing: ...

    def get_listing(self, user_id: int, listing_id: str) -> StoredListing | None: ...

    def save_media(
        self,
        media: StoredMedia,
        expected_draft_revision: int | None = None,
        expected_preparation_id: str | None = None,
    ) -> None: ...

    def list_media(self, listing_id: str) -> list[StoredMedia]: ...

    def replace_media(self, listing_id: str, media: list[StoredMedia]) -> None: ...

    def save_transfer(self, transfer: StoredTransfer) -> None: ...

    def get_transfer(self, user_id: int, transfer_id: str) -> StoredTransfer | None: ...

    def get_listing_transfer(self, listing_id: str) -> StoredTransfer | None: ...

    def clear_listing_transfer(self, listing_id: str) -> None: ...

    def update_transfer(self, transfer_id: str, status: str, error: str | None = None) -> None: ...

    def restart_transfer(self, transfer_id: str, task_id: str, status: str) -> None: ...

    def mark_listing_live(
        self,
        listing_id: str,
        item_id: str,
        revision: str | None,
        state: str = "live",
        publication_lease: datetime | None = None,
    ) -> None: ...

    def begin_listing_publication(
        self,
        listing_id: str,
        expected_draft_revision: int | None = None,
        expected_preparation_id: str | None = None,
    ) -> datetime: ...

    def mark_listing_publication_attempted(
        self, listing_id: str, publication_lease: datetime
    ) -> None: ...

    def abort_listing_publication(self, listing_id: str, publication_lease: datetime) -> None: ...

    def update_listing(
        self, listing_id: str, draft: dict[str, object], revision: str | None
    ) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _publication_started_at_is_stale(
    started_at: datetime | None, now: datetime | None = None
) -> bool:
    return bool(started_at and (now or _now()) - started_at >= PUBLICATION_LEASE_TIME)


def _publication_is_stale(listing: StoredListing, now: datetime | None = None) -> bool:
    return _publication_started_at_is_stale(listing.publication_started_at, now)


class MemoryEbayStore:
    def __init__(self) -> None:
        self.category_lists: dict[str, list[dict[str, str]]] = {}
        self.connections: dict[int, EbayConnection] = {}
        self.listings: dict[str, StoredListing] = {}
        self.media: dict[str, list[StoredMedia]] = {}
        self.transfers: dict[str, StoredTransfer] = {}
        self.drafts: dict[str, StoredDraft] = {}

    def get_category_list(self, key: str) -> list[dict[str, str]] | None:
        value = self.category_lists.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    def save_category_list(self, key: str, categories: list[dict[str, str]]) -> None:
        self.category_lists[key] = json.loads(json.dumps(categories))

    def healthcheck(self) -> None:
        return None

    def get_connection(self, user_id: int) -> EbayConnection | None:
        return self.connections.get(user_id)

    def connect(self, connection: EbayConnection) -> None:
        if any(
            current.ebay_user_id == connection.ebay_user_id
            and current.user_id != connection.user_id
            for current in self.connections.values()
        ):
            raise EbayAccountAlreadyConnected
        self.connections[connection.user_id] = connection

    def disconnect(self, user_id: int) -> None:
        self.connections.pop(user_id, None)

    def mark_authorization_expired(self, user_id: int) -> None:
        current = self.connections.get(user_id)
        if current:
            self.connections[user_id] = EbayConnection(**{**current.__dict__, "status": "expired"})

    def delete_ebay_user_data(self, ebay_user_id: str) -> None:
        user_ids = [
            item.user_id for item in self.connections.values() if item.ebay_user_id == ebay_user_id
        ]
        for user_id in user_ids:
            self.disconnect(user_id)
            listing_ids = [x.id for x in self.listings.values() if x.user_id == user_id]
            for listing_id in listing_ids:
                self.listings.pop(listing_id, None)
                self.media.pop(listing_id, None)
            self.transfers = {
                key: value for key, value in self.transfers.items() if value.user_id != user_id
            }

    def save_draft(
        self,
        user_id: int,
        draft_id: str,
        draft: dict[str, object],
        ebay_category: dict[str, object] | None,
        ebay_condition: dict[str, object] | None,
        photo_count: int,
        expected_revision: int | None = None,
        enforce_revision: bool = False,
    ) -> StoredDraft | None:
        current = self.drafts.get(draft_id)
        if current and current.user_id != user_id:
            return None
        if enforce_revision and (
            (expected_revision is None and current is not None)
            or (
                expected_revision is not None
                and (current is None or current.revision != expected_revision)
            )
        ):
            raise DraftRevisionConflict
        completed_listing = self.listings.get(draft_id)
        if completed_listing:
            if completed_listing.state == "publishing":
                raise DraftPublicationInProgress
            if completed_listing.ebay_item_id:
                return None
        draft = dict(draft)
        if current and (
            current.draft.get("generation_required", False)
            or (current.ebay_category or {}).get("category_id")
            != (ebay_category or {}).get("category_id")
        ):
            draft["generation_required"] = True
        saved = StoredDraft(
            id=draft_id,
            user_id=user_id,
            draft=draft,
            ebay_category=ebay_category,
            ebay_condition=ebay_condition,
            photo_count=photo_count,
            updated_at=_now(),
            revision=current.revision + 1 if current else 1,
        )
        self.drafts[draft_id] = saved
        return saved

    def get_draft(self, user_id: int, draft_id: str) -> StoredDraft | None:
        draft = self.drafts.get(draft_id)
        return draft if draft and draft.user_id == user_id else None

    def list_drafts(self, user_id: int) -> list[StoredDraft]:
        return sorted(
            (draft for draft in self.drafts.values() if draft.user_id == user_id),
            key=lambda draft: draft.updated_at,
            reverse=True,
        )

    def get_draft_listings(self, user_id: int, draft_ids: list[str]) -> dict[str, StoredListing]:
        wanted = set(draft_ids)
        return {
            listing.id: listing
            for listing in self.listings.values()
            if listing.user_id == user_id and listing.id in wanted
        }

    def delete_draft(self, user_id: int, draft_id: str) -> bool:
        draft = self.get_draft(user_id, draft_id)
        if not draft:
            return False
        listing_ids = [
            item.id for item in self.listings.values() if item.listing_draft_id == draft_id
        ]
        if any(self.listings[listing_id].state == "publishing" for listing_id in listing_ids):
            raise DraftPublicationInProgress
        for listing_id in listing_ids:
            listing = self.listings[listing_id]
            if listing.ebay_item_id:
                self.listings[listing_id] = StoredListing(
                    **{**listing.__dict__, "listing_draft_id": None}
                )
            else:
                self.listings.pop(listing_id, None)
                self.media.pop(listing_id, None)
                self.transfers = {
                    key: value
                    for key, value in self.transfers.items()
                    if value.listing_id != listing_id
                }
        self.drafts.pop(draft_id, None)
        return True

    def get_or_create_listing(
        self,
        user_id: int,
        request_id: str,
        custom_label: str,
        draft: dict[str, object],
        listing_draft_id: str | None = None,
        expected_draft_revision: int | None = None,
        preparation_id: str | None = None,
    ) -> StoredListing:
        if listing_draft_id and expected_draft_revision:
            source = self.get_draft(user_id, listing_draft_id)
            if not source or source.revision != expected_draft_revision:
                raise DraftChangedDuringPublication
        current = next(
            (
                item
                for item in self.listings.values()
                if item.user_id == user_id and item.request_id == request_id
            ),
            None,
        )
        if current:
            if current.ebay_item_id is not None:
                raise DraftChangedDuringPublication
            if current.state == "publishing":
                if _publication_is_stale(current):
                    if not preparation_id or preparation_id != current.preparation_id:
                        raise DraftChangedDuringPublication
                    return current
                raise DraftPublicationInProgress
            if (
                listing_draft_id
                and current.ebay_item_id is None
                and current.listing_draft_revision != expected_draft_revision
            ):
                self.media.pop(current.id, None)
            if listing_draft_id:
                current = StoredListing(
                    **{
                        **current.__dict__,
                        "draft": draft,
                        "listing_draft_id": current.listing_draft_id or listing_draft_id,
                        "listing_draft_revision": expected_draft_revision,
                        "preparation_id": preparation_id,
                    }
                )
                self.listings[current.id] = current
            return current
        listing = StoredListing(
            id=request_id,
            user_id=user_id,
            request_id=request_id,
            custom_label=custom_label,
            state="ready",
            draft=draft,
            ebay_item_id=None,
            revision=None,
            listing_draft_id=listing_draft_id,
            listing_draft_revision=expected_draft_revision,
            preparation_id=preparation_id,
        )
        self.listings[listing.id] = listing
        return listing

    def get_listing(self, user_id: int, listing_id: str) -> StoredListing | None:
        listing = self.listings.get(listing_id)
        return listing if listing and listing.user_id == user_id else None

    def save_media(
        self,
        media: StoredMedia,
        expected_draft_revision: int | None = None,
        expected_preparation_id: str | None = None,
    ) -> None:
        listing = self.listings[media.listing_id]
        if (
            expected_draft_revision is not None
            and listing.listing_draft_revision != expected_draft_revision
        ):
            raise DraftChangedDuringPublication
        if (
            expected_preparation_id is not None
            and listing.preparation_id != expected_preparation_id
        ):
            raise DraftChangedDuringPublication
        if listing.state == "publishing" and not _publication_is_stale(listing):
            raise DraftPublicationInProgress
        items = [x for x in self.media.get(media.listing_id, []) if x.position != media.position]
        items.append(media)
        self.media[media.listing_id] = sorted(items, key=lambda item: item.position)

    def list_media(self, listing_id: str) -> list[StoredMedia]:
        return list(self.media.get(listing_id, []))

    def replace_media(self, listing_id: str, media: list[StoredMedia]) -> None:
        if self.listings[listing_id].state == "publishing":
            raise DraftPublicationInProgress
        self.media[listing_id] = sorted(media, key=lambda item: item.position)

    def save_transfer(self, transfer: StoredTransfer) -> None:
        self.transfers[transfer.id] = transfer
        listing = self.listings[transfer.listing_id]
        self.listings[listing.id] = StoredListing(
            **{**listing.__dict__, "state": "seller_hub_transfer"}
        )

    def get_transfer(self, user_id: int, transfer_id: str) -> StoredTransfer | None:
        transfer = self.transfers.get(transfer_id)
        return transfer if transfer and transfer.user_id == user_id else None

    def get_listing_transfer(self, listing_id: str) -> StoredTransfer | None:
        return next((x for x in self.transfers.values() if x.listing_id == listing_id), None)

    def clear_listing_transfer(self, listing_id: str) -> None:
        self.transfers = {
            key: value for key, value in self.transfers.items() if value.listing_id != listing_id
        }
        listing = self.listings.get(listing_id)
        if listing and listing.state == "seller_hub_transfer":
            self.listings[listing_id] = StoredListing(**{**listing.__dict__, "state": "ready"})

    def update_transfer(self, transfer_id: str, status: str, error: str | None = None) -> None:
        transfer = self.transfers[transfer_id]
        self.transfers[transfer_id] = StoredTransfer(
            **{**transfer.__dict__, "status": status, "error": error, "updated_at": _now()}
        )

    def restart_transfer(self, transfer_id: str, task_id: str, status: str) -> None:
        transfer = self.transfers[transfer_id]
        self.transfers[transfer_id] = StoredTransfer(
            **{
                **transfer.__dict__,
                "task_id": task_id,
                "status": status,
                "error": None,
                "updated_at": _now(),
            }
        )

    def mark_listing_live(
        self,
        listing_id: str,
        item_id: str,
        revision: str | None,
        state: str = "live",
        publication_lease: datetime | None = None,
    ) -> None:
        listing = self.listings[listing_id]
        if listing.state == "publishing" and (
            publication_lease is None or listing.publication_started_at != publication_lease
        ):
            raise DraftPublicationInProgress
        if listing.state != "publishing" and publication_lease is not None:
            raise DraftPublicationInProgress
        if listing.listing_draft_id and listing.listing_draft_revision:
            source = self.drafts.get(listing.listing_draft_id)
            if not source or source.revision != listing.listing_draft_revision:
                raise DraftChangedDuringPublication
        self.listings[listing_id] = StoredListing(
            **{
                **listing.__dict__,
                "state": state,
                "ebay_item_id": item_id,
                "revision": revision,
                "publication_started_at": None,
                "publication_attempted": False,
            }
        )

    def begin_listing_publication(
        self,
        listing_id: str,
        expected_draft_revision: int | None = None,
        expected_preparation_id: str | None = None,
    ) -> datetime:
        listing = self.listings[listing_id]
        if (
            expected_draft_revision is not None
            and listing.listing_draft_revision != expected_draft_revision
        ):
            raise DraftChangedDuringPublication
        if (
            expected_preparation_id is not None
            and listing.preparation_id != expected_preparation_id
        ):
            raise DraftChangedDuringPublication
        if listing.state == "publishing" and not _publication_is_stale(listing):
            raise DraftPublicationInProgress
        if listing.listing_draft_id and listing.listing_draft_revision:
            source = self.drafts.get(listing.listing_draft_id)
            if not source or source.revision != listing.listing_draft_revision:
                raise DraftChangedDuringPublication
        publication_lease = _now()
        self.listings[listing_id] = StoredListing(
            **{
                **listing.__dict__,
                "state": "publishing",
                "publication_started_at": publication_lease,
                "publication_attempted": (
                    listing.publication_attempted if listing.state == "publishing" else False
                ),
            }
        )
        return publication_lease

    def mark_listing_publication_attempted(
        self, listing_id: str, publication_lease: datetime
    ) -> None:
        listing = self.listings[listing_id]
        if listing.state != "publishing" or listing.publication_started_at != publication_lease:
            raise DraftPublicationInProgress
        self.listings[listing_id] = StoredListing(
            **{**listing.__dict__, "publication_attempted": True}
        )

    def abort_listing_publication(self, listing_id: str, publication_lease: datetime) -> None:
        listing = self.listings.get(listing_id)
        if (
            listing
            and listing.state == "publishing"
            and listing.publication_started_at == publication_lease
        ):
            self.listings[listing_id] = StoredListing(
                **{
                    **listing.__dict__,
                    "state": "ready",
                    "publication_started_at": None,
                    "publication_attempted": False,
                }
            )

    def update_listing(
        self, listing_id: str, draft: dict[str, object], revision: str | None
    ) -> None:
        listing = self.listings[listing_id]
        if listing.state == "publishing":
            raise DraftPublicationInProgress
        self.listings[listing_id] = StoredListing(
            **{**listing.__dict__, "draft": draft, "revision": revision}
        )


class PostgresEbayStore:
    def _connect(self):
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise EbayStoreUnavailable(
                "DATABASE_URL is not configured", error_class="configuration"
            )
        try:
            return psycopg.connect(database_url)
        except psycopg.Error as error:
            raise _store_unavailable(error) from error

    def get_category_list(self, key: str) -> list[dict[str, str]] | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT categories FROM ebay_category_lists WHERE cache_key = %s", (key,)
                ).fetchone()
                return row[0] if row else None
        except psycopg.Error as error:
            raise _store_unavailable(error) from error

    def save_category_list(self, key: str, categories: list[dict[str, str]]) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO ebay_category_lists (cache_key, categories) "
                    "VALUES (%s, %s::jsonb) "
                    "ON CONFLICT (cache_key) DO NOTHING",
                    (key, json.dumps(categories)),
                )
        except psycopg.Error as error:
            raise _store_unavailable(error) from error

    def healthcheck(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("SELECT status FROM ebay_connections LIMIT 0")
                connection.execute(
                    "SELECT id, listing_draft_id, listing_draft_revision, "
                    "preparation_id, publication_started_at, publication_attempted "
                    "FROM ebay_listings LIMIT 0"
                )
                connection.execute("SELECT id, draft FROM listing_drafts LIMIT 0")
        except psycopg.Error as error:
            raise _store_unavailable(error) from error

    @staticmethod
    def _connection(row) -> EbayConnection | None:
        return EbayConnection(**row) if row else None

    @staticmethod
    def _listing(row) -> StoredListing | None:
        if not row:
            return None
        draft = row["draft"] if isinstance(row["draft"], dict) else json.loads(row["draft"])
        return StoredListing(**{**row, "draft": draft})

    @staticmethod
    def _draft(row) -> StoredDraft | None:
        if not row:
            return None
        values = dict(row)
        for name in ("draft", "ebay_category", "ebay_condition"):
            value = values[name]
            if value is not None and not isinstance(value, dict):
                values[name] = json.loads(value)
        return StoredDraft(**values)

    @staticmethod
    def _transfer(row) -> StoredTransfer | None:
        return StoredTransfer(**row) if row else None

    def get_connection(self, user_id: int) -> EbayConnection | None:
        return self._one_connection("WHERE user_id = %s", (user_id,))

    def _one_connection(self, clause: str, parameters: tuple[object, ...]):
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    "SELECT user_id, ebay_user_id, display_name, encrypted_refresh_token, "
                    f"status, connected_at FROM ebay_connections {clause}",
                    parameters,
                ).fetchone()
                return self._connection(row)
        except psycopg.Error as error:
            raise _store_unavailable(error) from error

    def connect(self, value: EbayConnection) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO ebay_connections
                    (user_id, ebay_user_id, display_name, encrypted_refresh_token, status,
                     connected_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id) DO UPDATE SET
                    ebay_user_id = EXCLUDED.ebay_user_id,
                    display_name = EXCLUDED.display_name,
                    encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                    status = 'connected', updated_at = CURRENT_TIMESTAMP""",
                    (
                        value.user_id,
                        value.ebay_user_id,
                        value.display_name,
                        value.encrypted_refresh_token,
                        value.status,
                        value.connected_at,
                    ),
                )
        except psycopg.errors.UniqueViolation as error:
            raise EbayAccountAlreadyConnected from error
        except psycopg.Error as error:
            raise _store_unavailable(error) from error

    def disconnect(self, user_id: int) -> None:
        self._execute("DELETE FROM ebay_connections WHERE user_id = %s", (user_id,))

    def mark_authorization_expired(self, user_id: int) -> None:
        self._execute(
            "UPDATE ebay_connections SET status = 'expired', updated_at = CURRENT_TIMESTAMP "
            "WHERE user_id = %s",
            (user_id,),
        )

    def delete_ebay_user_data(self, ebay_user_id: str) -> None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT user_id FROM ebay_connections WHERE ebay_user_id = %s",
                    (ebay_user_id,),
                ).fetchone()
                if row:
                    connection.execute("DELETE FROM ebay_listings WHERE user_id = %s", (row[0],))
                    connection.execute(
                        "DELETE FROM ebay_connections WHERE ebay_user_id = %s", (ebay_user_id,)
                    )
        except psycopg.Error as error:
            raise _store_unavailable(error) from error

    def save_draft(
        self,
        user_id: int,
        draft_id: str,
        draft: dict[str, object],
        ebay_category: dict[str, object] | None,
        ebay_condition: dict[str, object] | None,
        photo_count: int,
        expected_revision: int | None = None,
        enforce_revision: bool = False,
    ) -> StoredDraft | None:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                current = cursor.execute(
                    """SELECT revision FROM listing_drafts
                       WHERE id = %s AND user_id = %s FOR UPDATE""",
                    (draft_id, user_id),
                ).fetchone()
                if enforce_revision and (
                    (expected_revision is None and current is not None)
                    or (
                        expected_revision is not None
                        and (not current or current["revision"] != expected_revision)
                    )
                ):
                    raise DraftRevisionConflict
                listing = cursor.execute(
                    """SELECT state, ebay_item_id FROM ebay_listings
                       WHERE id = %s AND user_id = %s FOR UPDATE""",
                    (draft_id, user_id),
                ).fetchone()
                if listing:
                    if listing["state"] == "publishing":
                        raise DraftPublicationInProgress
                    if listing["ebay_item_id"] is not None:
                        return None
                row = cursor.execute(
                    """INSERT INTO listing_drafts
                    (id, user_id, draft, ebay_category, ebay_condition, photo_count)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                    draft = CASE WHEN
                        COALESCE((listing_drafts.draft->>'generation_required')::boolean, false)
                        OR (listing_drafts.ebay_category->>'category_id') IS DISTINCT FROM
                           (EXCLUDED.ebay_category->>'category_id')
                        THEN jsonb_set(EXCLUDED.draft, '{generation_required}', 'true'::jsonb)
                        ELSE EXCLUDED.draft END,
                    ebay_category = EXCLUDED.ebay_category,
                    ebay_condition = EXCLUDED.ebay_condition,
                    photo_count = EXCLUDED.photo_count,
                    updated_at = CURRENT_TIMESTAMP,
                    revision = listing_drafts.revision + 1
                    WHERE listing_drafts.user_id = EXCLUDED.user_id
                    RETURNING id::text, user_id, draft, ebay_category, ebay_condition,
                              photo_count, updated_at, revision""",
                    (
                        draft_id,
                        user_id,
                        json.dumps(draft),
                        json.dumps(ebay_category) if ebay_category else None,
                        json.dumps(ebay_condition) if ebay_condition else None,
                        photo_count,
                    ),
                ).fetchone()
                return self._draft(row)
        except psycopg.Error as error:
            raise _store_unavailable(error) from error

    def get_draft(self, user_id: int, draft_id: str) -> StoredDraft | None:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """SELECT id::text, user_id, draft, ebay_category, ebay_condition,
                              photo_count, updated_at, revision
                       FROM listing_drafts WHERE id = %s AND user_id = %s""",
                    (draft_id, user_id),
                ).fetchone()
                return self._draft(row)
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def list_drafts(self, user_id: int) -> list[StoredDraft]:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute(
                    """SELECT id::text, user_id, draft, ebay_category, ebay_condition,
                              photo_count, updated_at, revision
                       FROM listing_drafts WHERE user_id = %s ORDER BY updated_at DESC""",
                    (user_id,),
                ).fetchall()
                return [self._draft(row) for row in rows]
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def get_draft_listings(self, user_id: int, draft_ids: list[str]) -> dict[str, StoredListing]:
        if not draft_ids:
            return {}
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute(
                    """SELECT id::text, user_id, request_id::text, custom_label, state, draft,
                              ebay_item_id, revision, listing_draft_id::text,
                              listing_draft_revision, preparation_id::text, publication_started_at,
                              publication_attempted
                       FROM ebay_listings
                       WHERE user_id = %s AND id = ANY(%s::uuid[])""",
                    (user_id, draft_ids),
                ).fetchall()
                listings = [self._listing(row) for row in rows]
                return {listing.id: listing for listing in listings if listing is not None}
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def delete_draft(self, user_id: int, draft_id: str) -> bool:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                found = cursor.execute(
                    """SELECT 1 FROM listing_drafts
                       WHERE id = %s AND user_id = %s FOR UPDATE""",
                    (draft_id, user_id),
                ).fetchone()
                if not found:
                    return False
                listing = cursor.execute(
                    """SELECT id::text, state FROM ebay_listings
                       WHERE listing_draft_id = %s AND user_id = %s FOR UPDATE""",
                    (draft_id, user_id),
                ).fetchone()
                if listing and listing["state"] == "publishing":
                    raise DraftPublicationInProgress
                cursor.execute(
                    """DELETE FROM ebay_listings
                       WHERE listing_draft_id = %s AND user_id = %s
                         AND ebay_item_id IS NULL""",
                    (draft_id, user_id),
                )
                cursor.execute(
                    """UPDATE ebay_listings SET listing_draft_id = NULL
                       WHERE listing_draft_id = %s AND user_id = %s
                         AND ebay_item_id IS NOT NULL""",
                    (draft_id, user_id),
                )
                cursor.execute(
                    "DELETE FROM listing_drafts WHERE id = %s AND user_id = %s",
                    (draft_id, user_id),
                )
                return True
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def _execute(self, query: str, parameters: tuple[object, ...]) -> None:
        try:
            with self._connect() as connection:
                connection.execute(query, parameters)
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def get_or_create_listing(
        self,
        user_id: int,
        request_id: str,
        custom_label: str,
        draft: dict[str, object],
        listing_draft_id: str | None = None,
        expected_draft_revision: int | None = None,
        preparation_id: str | None = None,
    ) -> StoredListing:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                if listing_draft_id and expected_draft_revision:
                    source = cursor.execute(
                        """SELECT revision FROM listing_drafts
                           WHERE id = %s AND user_id = %s FOR UPDATE""",
                        (listing_draft_id, user_id),
                    ).fetchone()
                    if not source or source["revision"] != expected_draft_revision:
                        raise DraftChangedDuringPublication
                current_row = cursor.execute(
                    """SELECT id::text, user_id, request_id::text, custom_label, state, draft,
                              ebay_item_id, revision, listing_draft_id::text,
                              listing_draft_revision, preparation_id::text, publication_started_at,
                              publication_attempted
                       FROM ebay_listings
                       WHERE user_id = %s AND request_id = %s FOR UPDATE""",
                    (user_id, request_id),
                ).fetchone()
                current = self._listing(current_row)
                if current and current.ebay_item_id is not None:
                    raise DraftChangedDuringPublication
                if current and current.state == "publishing":
                    if _publication_is_stale(current):
                        if not preparation_id or preparation_id != current.preparation_id:
                            raise DraftChangedDuringPublication
                        return current
                    raise DraftPublicationInProgress
                if (
                    current
                    and listing_draft_id
                    and current.ebay_item_id is None
                    and current.listing_draft_revision != expected_draft_revision
                ):
                    cursor.execute(
                        "DELETE FROM ebay_media WHERE listing_id = %s",
                        (current.id,),
                    )
                row = cursor.execute(
                    """INSERT INTO ebay_listings
                    (id, user_id, request_id, custom_label, state, draft, listing_draft_id,
                     listing_draft_revision, preparation_id)
                    VALUES (%s, %s, %s, %s, 'ready', %s, %s, %s, %s)
                    ON CONFLICT (user_id, request_id) DO UPDATE SET
                    listing_draft_id = COALESCE(ebay_listings.listing_draft_id,
                                                EXCLUDED.listing_draft_id),
                    listing_draft_revision = EXCLUDED.listing_draft_revision,
                    preparation_id = EXCLUDED.preparation_id,
                    draft = EXCLUDED.draft,
                    updated_at = CURRENT_TIMESTAMP
                    WHERE ebay_listings.state <> 'publishing'
                    RETURNING id::text, user_id, request_id::text, custom_label, state, draft,
                              ebay_item_id, revision, listing_draft_id::text,
                              listing_draft_revision, preparation_id::text, publication_started_at,
                              publication_attempted""",
                    (
                        request_id,
                        user_id,
                        request_id,
                        custom_label,
                        json.dumps(draft),
                        listing_draft_id,
                        expected_draft_revision,
                        preparation_id,
                    ),
                ).fetchone()
                if not row:
                    raise DraftPublicationInProgress
                return self._listing(row)
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def get_listing(self, user_id: int, listing_id: str) -> StoredListing | None:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    """SELECT id::text, user_id, request_id::text, custom_label, state, draft,
                              ebay_item_id, revision, listing_draft_id::text,
                              listing_draft_revision, preparation_id::text, publication_started_at,
                              publication_attempted
                       FROM ebay_listings
                       WHERE id = %s AND user_id = %s""",
                    (listing_id, user_id),
                ).fetchone()
                return self._listing(row)
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def save_media(
        self,
        media: StoredMedia,
        expected_draft_revision: int | None = None,
        expected_preparation_id: str | None = None,
    ) -> None:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                listing = cursor.execute(
                    "SELECT state, listing_draft_revision, preparation_id::text, "
                    "publication_started_at "
                    "FROM ebay_listings WHERE id = %s FOR UPDATE",
                    (media.listing_id,),
                ).fetchone()
                if (
                    expected_draft_revision is not None
                    and listing
                    and listing["listing_draft_revision"] != expected_draft_revision
                ):
                    raise DraftChangedDuringPublication
                if (
                    expected_preparation_id is not None
                    and listing
                    and listing["preparation_id"] != expected_preparation_id
                ):
                    raise DraftChangedDuringPublication
                if (
                    listing
                    and listing["state"] == "publishing"
                    and not _publication_started_at_is_stale(listing["publication_started_at"])
                ):
                    raise DraftPublicationInProgress
                cursor.execute(
                    """INSERT INTO ebay_media
                    (listing_id, position, image_id, image_url, expires_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (listing_id, position) DO UPDATE SET
                    image_id = EXCLUDED.image_id, image_url = EXCLUDED.image_url,
                    expires_at = EXCLUDED.expires_at""",
                    (
                        media.listing_id,
                        media.position,
                        media.image_id,
                        media.image_url,
                        media.expires_at,
                    ),
                )
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def list_media(self, listing_id: str) -> list[StoredMedia]:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                return [
                    StoredMedia(**row)
                    for row in cursor.execute(
                        """SELECT listing_id::text, position, image_id, image_url, expires_at
                        FROM ebay_media WHERE listing_id = %s ORDER BY position""",
                        (listing_id,),
                    ).fetchall()
                ]
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def replace_media(self, listing_id: str, media: list[StoredMedia]) -> None:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                listing = cursor.execute(
                    "SELECT state FROM ebay_listings WHERE id = %s FOR UPDATE",
                    (listing_id,),
                ).fetchone()
                if listing and listing["state"] == "publishing":
                    raise DraftPublicationInProgress
                connection.execute("DELETE FROM ebay_media WHERE listing_id = %s", (listing_id,))
                cursor.executemany(
                    """INSERT INTO ebay_media
                    (listing_id, position, image_id, image_url, expires_at)
                    VALUES (%s, %s, %s, %s, %s)""",
                    [
                        (
                            item.listing_id,
                            item.position,
                            item.image_id,
                            item.image_url,
                            item.expires_at,
                        )
                        for item in media
                    ],
                )
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def save_transfer(self, transfer: StoredTransfer) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO ebay_feed_transfers
                    (id, listing_id, user_id, task_id, status, safe_error, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        transfer.id,
                        transfer.listing_id,
                        transfer.user_id,
                        transfer.task_id,
                        transfer.status,
                        transfer.error,
                        transfer.updated_at,
                    ),
                )
                connection.execute(
                    "UPDATE ebay_listings SET state = 'seller_hub_transfer', "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (transfer.listing_id,),
                )
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def get_transfer(self, user_id: int, transfer_id: str) -> StoredTransfer | None:
        return self._one_transfer("WHERE id = %s AND user_id = %s", (transfer_id, user_id))

    def get_listing_transfer(self, listing_id: str) -> StoredTransfer | None:
        return self._one_transfer("WHERE listing_id = %s", (listing_id,))

    def clear_listing_transfer(self, listing_id: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM ebay_feed_transfers WHERE listing_id = %s", (listing_id,)
                )
                connection.execute(
                    "UPDATE ebay_listings SET state = 'ready', updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = %s AND state = 'seller_hub_transfer'",
                    (listing_id,),
                )
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def _one_transfer(self, clause: str, parameters: tuple[object, ...]):
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    "SELECT id::text, listing_id::text, user_id, task_id, status, "
                    f"safe_error AS error, updated_at FROM ebay_feed_transfers {clause}",
                    parameters,
                ).fetchone()
                return self._transfer(row)
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def update_transfer(self, transfer_id: str, status: str, error: str | None = None) -> None:
        self._execute(
            "UPDATE ebay_feed_transfers SET status = %s, safe_error = %s, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (status, error, transfer_id),
        )

    def restart_transfer(self, transfer_id: str, task_id: str, status: str) -> None:
        self._execute(
            "UPDATE ebay_feed_transfers SET task_id = %s, status = %s, safe_error = NULL, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (task_id, status, transfer_id),
        )

    @staticmethod
    def _lock_listing_source(cursor, listing_id: str):
        initial = cursor.execute(
            """SELECT listing_draft_id::text, listing_draft_revision,
                      preparation_id::text, publication_started_at, publication_attempted
               FROM ebay_listings WHERE id = %s""",
            (listing_id,),
        ).fetchone()
        if not initial:
            return None
        if initial["listing_draft_id"] and initial["listing_draft_revision"]:
            source = cursor.execute(
                "SELECT revision FROM listing_drafts WHERE id = %s FOR UPDATE",
                (initial["listing_draft_id"],),
            ).fetchone()
            if not source or source["revision"] != initial["listing_draft_revision"]:
                raise DraftChangedDuringPublication
        listing = cursor.execute(
            """SELECT state, listing_draft_id::text, listing_draft_revision,
                      preparation_id::text, publication_started_at, publication_attempted
               FROM ebay_listings WHERE id = %s FOR UPDATE""",
            (listing_id,),
        ).fetchone()
        if (
            not listing
            or listing["listing_draft_id"] != initial["listing_draft_id"]
            or listing["listing_draft_revision"] != initial["listing_draft_revision"]
            or listing["preparation_id"] != initial["preparation_id"]
        ):
            raise DraftChangedDuringPublication
        return listing

    def begin_listing_publication(
        self,
        listing_id: str,
        expected_draft_revision: int | None = None,
        expected_preparation_id: str | None = None,
    ) -> datetime:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                listing = self._lock_listing_source(cursor, listing_id)
                if not listing:
                    raise DraftChangedDuringPublication
                if (
                    expected_draft_revision is not None
                    and listing["listing_draft_revision"] != expected_draft_revision
                ):
                    raise DraftChangedDuringPublication
                if (
                    expected_preparation_id is not None
                    and listing["preparation_id"] != expected_preparation_id
                ):
                    raise DraftChangedDuringPublication
                if listing["state"] == "publishing" and not _publication_started_at_is_stale(
                    listing["publication_started_at"]
                ):
                    raise DraftPublicationInProgress
                if listing["state"] not in {"ready", "publishing"}:
                    raise DraftChangedDuringPublication
                updated = cursor.execute(
                    "UPDATE ebay_listings SET state = 'publishing', "
                    "publication_started_at = CURRENT_TIMESTAMP, "
                    "publication_attempted = CASE WHEN state = 'publishing' "
                    "THEN publication_attempted ELSE FALSE END, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = %s "
                    "RETURNING publication_started_at",
                    (listing_id,),
                ).fetchone()
                return updated["publication_started_at"]
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def mark_listing_publication_attempted(
        self, listing_id: str, publication_lease: datetime
    ) -> None:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                updated = cursor.execute(
                    "UPDATE ebay_listings SET publication_attempted = TRUE, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = %s AND state = 'publishing' "
                    "AND publication_started_at = %s RETURNING publication_started_at",
                    (listing_id, publication_lease),
                ).fetchone()
                if not updated:
                    raise DraftPublicationInProgress
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def abort_listing_publication(self, listing_id: str, publication_lease: datetime) -> None:
        self._execute(
            "UPDATE ebay_listings SET state = 'ready', publication_started_at = NULL, "
            "publication_attempted = FALSE, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND state = 'publishing' AND publication_started_at = %s",
            (listing_id, publication_lease),
        )

    def mark_listing_live(
        self,
        listing_id: str,
        item_id: str,
        revision: str | None,
        state: str = "live",
        publication_lease: datetime | None = None,
    ) -> None:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                listing = self._lock_listing_source(cursor, listing_id)
                if not listing:
                    raise DraftChangedDuringPublication
                if listing["state"] == "publishing" and (
                    publication_lease is None
                    or listing["publication_started_at"] != publication_lease
                ):
                    raise DraftPublicationInProgress
                if listing["state"] != "publishing" and publication_lease is not None:
                    raise DraftPublicationInProgress
                query = (
                    "UPDATE ebay_listings SET state = %s, ebay_item_id = %s, revision = %s, "
                    "publication_started_at = NULL, publication_attempted = FALSE, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = %s"
                )
                parameters: tuple[object, ...] = (state, item_id, revision, listing_id)
                if publication_lease is not None:
                    query += " AND state = 'publishing' AND publication_started_at = %s"
                    parameters += (publication_lease,)
                cursor.execute(query, parameters)
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error

    def update_listing(
        self, listing_id: str, draft: dict[str, object], revision: str | None
    ) -> None:
        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                listing = cursor.execute(
                    "SELECT state FROM ebay_listings WHERE id = %s FOR UPDATE",
                    (listing_id,),
                ).fetchone()
                if listing and listing["state"] == "publishing":
                    raise DraftPublicationInProgress
                cursor.execute(
                    "UPDATE ebay_listings SET draft = %s, revision = %s, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (json.dumps(draft), revision, listing_id),
                )
        except psycopg.Error as error:
            raise EbayStoreUnavailable("eBay data store is unavailable") from error
