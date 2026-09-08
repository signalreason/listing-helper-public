import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.ebay_api import EbayApiError
from app.ebay_models import BusinessPolicies, EbayAspect, EbayIssue, TradingResult
from app.ebay_routes import _safe_feed_error
from app.ebay_store import (
    DraftChangedDuringPublication,
    EbayAccountAlreadyConnected,
    EbayConnection,
    EbayStoreUnavailable,
    MemoryEbayStore,
    StoredListing,
    StoredMedia,
    StoredTransfer,
)
from app.models import (
    DraftEbayCategory,
    DraftEbayCondition,
    ItemSpecific,
    PackageDimensions,
    PackageWeight,
)
from tests.support import (
    StubEbayGateway,
    StubNotificationVerifier,
    authenticated_client,
    jpeg_bytes,
    listing_draft,
)
from tests.test_ebay_api import ebay_listing


def connect_store(client, ebay_store, user, *, refresh_token: str = "refresh-secret"):
    ebay_store.connect(
        EbayConnection(
            user_id=user.id,
            ebay_user_id=f"ebay-{user.id}",
            display_name=f"seller-{user.id}",
            encrypted_refresh_token=client.app.state.ebay_token_cipher.encrypt(refresh_token),
            status="connected",
            connected_at=datetime.now(UTC),
        )
    )


def save_transfer_draft(store, user, draft_id: str, photo_count: int = 1):
    store.save_draft(
        user.id,
        draft_id,
        listing_draft().model_dump(mode="json"),
        DraftEbayCategory(
            category_id="175786", name="Sweaters", path="Women > Sweaters"
        ).model_dump(mode="json"),
        DraftEbayCondition(condition_id=3000, name="Pre-owned").model_dump(mode="json"),
        photo_count,
    )
    return {"draft_id": draft_id}


def test_connection_status_does_not_expose_saved_token() -> None:
    ebay_store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=ebay_store)
    connect_store(client, ebay_store, user)

    response = client.get("/api/ebay/connection")

    assert response.status_code == 200
    assert response.json()["display_name"] == f"seller-{user.id}"
    assert "token" not in response.text.lower()
    assert "secret" not in response.text.lower()


def test_oauth_callback_binds_user_and_encrypts_refresh_token() -> None:
    ebay_store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=ebay_store)
    start = client.post("/api/ebay/oauth/start")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]

    response = client.get(
        "/api/ebay/oauth/callback",
        params={"code": "authorization-code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code == 303
    connection = ebay_store.get_connection(user.id)
    assert connection.ebay_user_id == "immutable-ebay-user"
    assert connection.display_name == "seller-name"
    assert connection.encrypted_refresh_token != "refresh-secret"
    assert "refresh-secret" not in connection.encrypted_refresh_token


def test_oauth_callback_logs_safe_token_exchange_failure(caplog) -> None:
    class FailingGateway(StubEbayGateway):
        def exchange_code(self, code: str):
            raise EbayApiError("provider response that must not be logged")

    client, _, _ = authenticated_client(ebay_gateway=FailingGateway())
    start = client.post("/api/ebay/oauth/start")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]

    with caplog.at_level(logging.WARNING, logger="app.ebay.oauth"):
        response = client.get(
            "/api/ebay/oauth/callback",
            params={"code": "secret-authorization-code", "state": state},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/?ebay=connection-failed"
    assert "category=token_exchange_failed" in caplog.text
    assert "secret-authorization-code" not in caplog.text
    assert "provider response" not in caplog.text


def test_application_token_failure_keeps_safe_category_contract_and_seller_connection() -> None:
    class FailingCategoryGateway(StubEbayGateway):
        def category_suggestions(self, query: str):
            raise EbayApiError(
                "private provider response",
                failure_class="authorization",
                provider_status=401,
            )

        def category_requirements(self, category_id: str):
            raise EbayApiError(
                "private provider response",
                failure_class="authorization",
                provider_status=401,
            )

    ebay_store = MemoryEbayStore()
    client, _, user = authenticated_client(
        ebay_gateway=FailingCategoryGateway(), ebay_store=ebay_store
    )
    connect_store(client, ebay_store, user)

    suggestions = client.get("/api/ebay/categories", params={"q": "private query"})
    requirements = client.get("/api/ebay/categories/175786/requirements")

    assert suggestions.status_code == 502
    assert suggestions.json()["code"] == "ebay_request_failed"
    assert suggestions.json()["detail"] == "eBay category search failed."
    assert requirements.status_code == 502
    assert requirements.json()["code"] == "ebay_request_failed"
    assert requirements.json()["detail"] == "eBay category details failed."
    assert ebay_store.get_connection(user.id).status == "connected"
    assert "reconnect" not in suggestions.text.lower()
    assert "reconnect" not in requirements.text.lower()


def test_one_ebay_account_cannot_be_connected_to_two_app_users() -> None:
    store = MemoryEbayStore()
    now = datetime.now(UTC)
    store.connect(EbayConnection(1, "same-user", "one", "token-1", "connected", now))

    with pytest.raises(EbayAccountAlreadyConnected):
        store.connect(EbayConnection(2, "same-user", "two", "token-2", "connected", now))


def test_new_csv_handoffs_are_retired() -> None:
    ebay_store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=ebay_store)
    connect_store(client, ebay_store, user)

    response = client.post("/api/ebay/draft-transfers")

    assert response.status_code == 410
    assert response.json()["code"] == "seller_hub_csv_retired"


def test_feed_error_reports_ebay_field_message_without_echoing_listing_row() -> None:
    result = (
        "Action,Title,ErrorCode,ErrorMessage\n"
        'Draft,"Private listing title",21919188,"Enter a valid value for Size"\n'
    )

    error = _safe_feed_error(result)

    assert error == (
        "eBay rejected the draft: Enter a valid value for Size (eBay code 21919188) "
        "Correct the listed fields and try again."
    )
    assert "Private listing title" not in error


def seed_historical_transfer(store, user, status: str = "READY_FOR_UPLOAD") -> StoredTransfer:
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    listing = store.get_or_create_listing(
        user.id,
        draft_id,
        "LA-1-historical",
        ebay_listing().model_dump(mode="json"),
        draft_id,
    )
    store.save_media(StoredMedia(listing.id, 1, "image-1", "https://img.test/1.jpg", None))
    transfer = StoredTransfer(
        id=str(uuid.uuid4()),
        listing_id=listing.id,
        user_id=user.id,
        task_id=f"manual-{listing.id}",
        status=status,
        error="Historical eBay error." if status == "FAILED" else None,
        updated_at=datetime.now(UTC),
    )
    store.save_transfer(transfer)
    return transfer


def test_historical_transfer_status_and_csv_download_remain_available() -> None:
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    transfer = seed_historical_transfer(store, user)

    status = client.get(f"/api/ebay/draft-transfers/{transfer.id}")
    response = client.get(f"/api/ebay/draft-transfers/{transfer.id}/file")

    assert status.status_code == 200
    assert status.json()["status"] == "READY_FOR_UPLOAD"
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert re.fullmatch(
        r'attachment; filename="eBay-draft-listing-template-'
        r'[A-Z][a-z]{2}-\d{2}-\d{4}(?:-\d{2}){3}\.csv"',
        response.headers["content-disposition"],
    )


def test_completed_historical_transfer_retains_its_source_draft() -> None:
    gateway = StubEbayGateway()
    gateway.feed_status = "COMPLETED"
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_gateway=gateway, ebay_store=store)
    connect_store(client, store, user)
    transfer = seed_historical_transfer(store, user, "QUEUED")

    response = client.get(f"/api/ebay/draft-transfers/{transfer.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert store.get_draft(user.id, transfer.listing_id) is not None
    assert store.get_listing(user.id, transfer.listing_id).listing_draft_id == transfer.listing_id


def test_deletion_notification_requires_signature_and_removes_ebay_data(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_NOTIFICATION_VERIFICATION_TOKEN", "v" * 32)
    monkeypatch.setenv(
        "EBAY_NOTIFICATION_ENDPOINT_URL", "https://app.example/api/ebay/account-deletion"
    )
    ebay_store = MemoryEbayStore()
    verifier = StubNotificationVerifier(valid=True)
    client, _, user = authenticated_client(ebay_store=ebay_store, notification_verifier=verifier)
    connect_store(client, ebay_store, user)
    listing_id = str(uuid.uuid4())
    ebay_store.get_or_create_listing(user.id, listing_id, "LA-1-test", ebay_listing().model_dump())
    payload = {
        "metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION"},
        "notification": {"data": {"userId": f"ebay-{user.id}"}},
    }

    response = client.post(
        "/api/ebay/account-deletion",
        content=json.dumps(payload, separators=(",", ":")),
        headers={"X-EBAY-SIGNATURE": "signed"},
    )

    assert response.status_code == 204
    assert ebay_store.get_connection(user.id) is None
    assert ebay_store.get_listing(user.id, listing_id) is None
    assert verifier.calls[0][0] == json.dumps(payload, separators=(",", ":")).encode()


def test_invalid_deletion_signature_is_rejected() -> None:
    client, _, _ = authenticated_client(notification_verifier=StubNotificationVerifier(valid=False))
    response = client.post(
        "/api/ebay/account-deletion",
        json={"metadata": {}, "notification": {}},
        headers={"X-EBAY-SIGNATURE": "bad"},
    )

    assert response.status_code == 412


class StubTrading:
    def __init__(self) -> None:
        self.calls = []
        self.current_revision = "revision-1"
        self.current_listing = None
        self.current_image_urls = []
        self.verify_result = TradingResult(acknowledged=True, fees={"InsertionFee": "0.00"})
        self.publish_result = TradingResult(
            acknowledged=True, item_id="ebay-item-1", revision="revision-1"
        )
        self.verify_error = None
        self.publish_error = None

    def verify(self, *args):
        self.calls.append("verify")
        if self.verify_error:
            raise self.verify_error
        return self.verify_result

    def publish(self, *args):
        self.calls.append("publish")
        if self.publish_error:
            raise self.publish_error
        return self.publish_result

    def get(self, *args):
        self.calls.append("get")
        return TradingResult(
            acknowledged=True,
            item_id="ebay-item-1",
            revision=self.current_revision,
            raw_item_xml="<Item />",
            current_listing=self.current_listing,
            current_image_urls=self.current_image_urls,
        )

    def revise(self, *args):
        self.calls.append("revise")
        return TradingResult(acknowledged=True, item_id="ebay-item-1", revision="revision-2")

    def end(self, *args):
        self.calls.append("end")
        return TradingResult(acknowledged=True, item_id="ebay-item-1")

    def relist(self, *args):
        self.calls.append("relist")
        return TradingResult(acknowledged=True, item_id="ebay-item-2", revision="revision-3")


def live_listing():
    return ebay_listing().model_copy(
        update={
            "business_policies": BusinessPolicies(
                fulfillment_policy_id="shipping",
                payment_policy_id="payment",
                return_policy_id="returns",
            ),
            "package_weight": PackageWeight(pounds=1, ounces=4),
            "package_dimensions": PackageDimensions(length=12, width=8, height=3),
        }
    )


def seed_live_ready_listing(client, store, user):
    listing_id = str(uuid.uuid4())
    source = store.save_draft(user.id, listing_id, {}, None, None, 1)
    assert source is not None
    listing = store.get_or_create_listing(
        user.id,
        listing_id,
        "LA-1-live",
        live_listing().model_dump(mode="json"),
        listing_id,
        source.revision,
        "preparation-id",
    )
    store.save_media(StoredMedia(listing.id, 1, "image-1", "https://img.test/1.jpg", None))
    return listing


def publish_listing(client, listing):
    return client.post(
        f"/api/ebay/listings/{listing.id}/publish",
        json={
            "draft_revision": listing.listing_draft_revision,
            "preparation_id": listing.preparation_id,
        },
    )


def live_preparation_data(draft_id: str) -> dict[str, str]:
    return {
        "draft_id": draft_id,
        "draft_revision": "1",
        "fulfillment_policy_id": "shipping",
        "payment_policy_id": "payment",
        "return_policy_id": "returns",
        "package_weight_pounds": "1",
        "package_weight_ounces": "4",
        "package_length": "12",
        "package_width": "8",
        "package_height": "3",
    }


def publish_prepared(client, prepared):
    payload = prepared.json()
    return client.post(
        f"/api/ebay/listings/{payload['listing_id']}/publish",
        json={
            "draft_revision": payload["draft_revision"],
            "preparation_id": payload["preparation_id"],
        },
    )


def test_direct_publish_is_disabled_by_default() -> None:
    client, _, _ = authenticated_client()

    response = client.post(f"/api/ebay/listings/{uuid.uuid4()}/publish")

    assert response.status_code == 404
    assert response.json()["code"] == "direct_publish_disabled"


def test_direct_publish_rejects_an_unversioned_legacy_preparation(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = MemoryEbayStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing_id = str(uuid.uuid4())
    listing = store.get_or_create_listing(
        user.id, listing_id, "LA-1-legacy", live_listing().model_dump(mode="json")
    )
    store.save_media(StoredMedia(listing.id, 1, "image-1", "https://img.test/1.jpg", None))

    response = client.post(f"/api/ebay/listings/{listing.id}/publish")

    assert response.status_code == 409
    assert response.json()["code"] == "draft_changed"
    assert trading.calls == []
    assert store.get_listing(user.id, listing.id).state == "ready"


def test_direct_publish_always_verifies_first(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = MemoryEbayStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)

    response = publish_listing(client, listing)

    assert response.status_code == 200
    assert trading.calls == ["verify", "publish"]
    assert store.get_listing(user.id, listing.id).ebay_item_id == "ebay-item-1"
    assert response.json()["listing_url"] == "https://www.ebay.com/itm/ebay-item-1"
    assert response.json()["seller_hub_url"] == "https://www.ebay.com/sh/lst/active"

    repeated = publish_listing(client, listing)

    assert repeated.status_code == 200
    assert repeated.json()["item_id"] == "ebay-item-1"
    assert trading.calls == ["verify", "publish"]


def test_verification_rejection_returns_field_error_and_does_not_publish(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = MemoryEbayStore()
    trading = StubTrading()
    trading.verify_result = TradingResult(
        acknowledged=False,
        issues=[
            EbayIssue(
                severity="error",
                code="21919188",
                message="Enter a valid value for Size.",
            )
        ],
    )
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)

    response = publish_listing(client, listing)

    assert response.status_code == 422
    assert response.json()["code"] == "ebay_verification_failed"
    assert "Size" in response.json()["detail"]
    assert trading.calls == ["verify"]


def test_verification_transport_failure_releases_recovery_state(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = MemoryEbayStore()
    trading = StubTrading()
    trading.verify_error = EbayApiError("private verification transport failure")
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)

    response = publish_listing(client, listing)

    assert response.status_code == 502
    assert response.json()["code"] == "ebay_verification_unavailable"
    assert "private verification" not in response.text
    assert store.get_listing(user.id, listing.id).state == "ready"


def test_unacknowledged_publication_never_returns_http_success(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = MemoryEbayStore()
    trading = StubTrading()
    trading.publish_result = TradingResult(
        acknowledged=False,
        issues=[EbayIssue(severity="error", code="219", message="Correct the price.")],
    )
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)

    response = publish_listing(client, listing)

    assert response.status_code == 422
    assert response.json()["code"] == "ebay_publication_failed"
    assert "Correct the price" in response.json()["detail"]
    assert store.get_listing(user.id, listing.id).ebay_item_id is None
    assert store.get_listing(user.id, listing.id).state == "ready"


def test_uncertain_publication_keeps_recovery_lease_and_hides_provider_detail(
    monkeypatch,
) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    trading = StubTrading()
    trading.publish_error = EbayApiError("secret-token private provider response")
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    prepared = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    response = publish_prepared(client, prepared)
    reopened = client.get(f"/api/drafts/{draft_id}")
    deleted = client.delete(f"/api/drafts/{draft_id}")

    assert response.status_code == 502
    assert response.json()["code"] == "ebay_publish_uncertain"
    assert "Retry" in response.json()["detail"]
    assert "secret-token" not in response.text
    assert store.get_listing(user.id, draft_id).state == "publishing"
    assert reopened.status_code == 200
    assert reopened.json()["publication_recovery_pending"] is True
    assert reopened.json()["publication_recovery_policies"] == {
        "fulfillment_policy_id": "shipping",
        "payment_policy_id": "payment",
        "return_policy_id": "returns",
    }
    assert (
        reopened.json()["publication_recovery_preparation_id"] == prepared.json()["preparation_id"]
    )
    assert deleted.status_code == 409
    assert deleted.json()["code"] == "draft_publication_in_progress"


def test_reload_failure_keeps_a_renewed_uncertain_publication_lease(monkeypatch) -> None:
    class ReloadFailureStore(MemoryEbayStore):
        fail_after_begin = False
        fail_next_get = False

        def begin_listing_publication(
            self,
            listing_id: str,
            expected_draft_revision: int | None = None,
            expected_preparation_id: str | None = None,
        ) -> datetime:
            lease = super().begin_listing_publication(
                listing_id, expected_draft_revision, expected_preparation_id
            )
            self.fail_next_get = self.fail_after_begin
            return lease

        def get_listing(self, user_id: int, listing_id: str):
            if self.fail_next_get:
                self.fail_next_get = False
                raise EbayStoreUnavailable
            return super().get_listing(user_id, listing_id)

    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = ReloadFailureStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)
    first_lease = MemoryEbayStore.begin_listing_publication(store, listing.id)
    store.mark_listing_publication_attempted(listing.id, first_lease)
    interrupted = store.get_listing(user.id, listing.id)
    assert interrupted is not None
    store.listings[listing.id] = StoredListing(
        **{
            **interrupted.__dict__,
            "publication_started_at": datetime.now(UTC) - timedelta(minutes=16),
        }
    )
    store.fail_after_begin = True

    response = publish_listing(client, listing)

    assert response.status_code == 503
    assert trading.calls == []
    recovered = store.listings[listing.id]
    assert recovered.state == "publishing"
    assert recovered.publication_attempted is True
    assert recovered.publication_started_at > datetime.now(UTC) - timedelta(minutes=1)


def test_resumed_uncertain_publication_keeps_lease_after_verification_rejection(
    monkeypatch,
) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    trading = StubTrading()
    trading.publish_error = EbayApiError("uncertain add response")
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)

    first = publish_listing(client, listing)
    interrupted = store.get_listing(user.id, listing.id)
    assert interrupted is not None
    assert interrupted.publication_attempted is True
    store.listings[listing.id] = StoredListing(
        **{
            **interrupted.__dict__,
            "publication_started_at": datetime.now(UTC) - timedelta(minutes=16),
        }
    )
    trading.publish_error = None
    trading.verify_result = TradingResult(
        acknowledged=False,
        issues=[EbayIssue(severity="error", code="219", message="Correct the title.")],
    )

    retry = publish_listing(client, listing)

    assert first.status_code == 502
    assert retry.status_code == 422
    assert retry.json()["code"] == "ebay_verification_failed_recovery"
    recovered = store.get_listing(user.id, listing.id)
    assert recovered is not None
    assert recovered.state == "publishing"
    assert recovered.publication_attempted is True


def test_resumed_uncertain_publication_keeps_lease_after_verification_transport_failure(
    monkeypatch,
) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    trading = StubTrading()
    trading.publish_error = EbayApiError("uncertain add response")
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)

    first = publish_listing(client, listing)
    interrupted = store.get_listing(user.id, listing.id)
    assert interrupted is not None
    assert interrupted.publication_attempted is True
    store.listings[listing.id] = StoredListing(
        **{
            **interrupted.__dict__,
            "publication_started_at": datetime.now(UTC) - timedelta(minutes=16),
        }
    )
    trading.publish_error = None
    trading.verify_error = EbayApiError("private verification transport failure")

    retry = publish_listing(client, listing)

    assert first.status_code == 502
    assert retry.status_code == 502
    assert retry.json()["code"] == "ebay_verification_unavailable_recovery"
    assert "private verification" not in retry.text
    recovered = store.get_listing(user.id, listing.id)
    assert recovered is not None
    assert recovered.state == "publishing"
    assert recovered.publication_attempted is True


def test_resumed_uncertain_publication_keeps_lease_after_add_rejection(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    trading = StubTrading()
    trading.publish_error = EbayApiError("uncertain add response")
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)

    first = publish_listing(client, listing)
    interrupted = store.get_listing(user.id, listing.id)
    assert interrupted is not None
    store.listings[listing.id] = StoredListing(
        **{
            **interrupted.__dict__,
            "publication_started_at": datetime.now(UTC) - timedelta(minutes=16),
        }
    )
    trading.publish_error = None
    trading.publish_result = TradingResult(
        acknowledged=False,
        issues=[EbayIssue(severity="error", code="219", message="Retry rejected.")],
    )

    retry = publish_listing(client, listing)

    assert first.status_code == 502
    assert retry.status_code == 422
    assert retry.json()["code"] == "ebay_publication_failed_recovery"
    recovered = store.get_listing(user.id, listing.id)
    assert recovered is not None
    assert recovered.state == "publishing"
    assert recovered.publication_attempted is True


def test_publication_reloads_the_snapshot_after_acquiring_its_lease(monkeypatch) -> None:
    class ReprepareOnBeginStore(MemoryEbayStore):
        def begin_listing_publication(
            self,
            listing_id: str,
            expected_draft_revision: int | None = None,
            expected_preparation_id: str | None = None,
        ) -> datetime:
            listing = self.listings[listing_id]
            replacement = {**listing.draft, "title": "Newly prepared title"}
            self.update_listing(listing_id, replacement, listing.revision)
            self.replace_media(
                listing_id,
                [StoredMedia(listing_id, 1, "image-2", "https://img.test/new.jpg", None)],
            )
            return super().begin_listing_publication(
                listing_id, expected_draft_revision, expected_preparation_id
            )

    class CapturingTrading(StubTrading):
        published_title = None
        published_urls = None

        def publish(self, *args):
            self.published_title = args[1].title
            self.published_urls = args[3]
            return super().publish(*args)

    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = ReprepareOnBeginStore()
    trading = CapturingTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)

    response = publish_listing(client, listing)

    assert response.status_code == 200
    assert trading.published_title == "Newly prepared title"
    assert trading.published_urls == ["https://img.test/new.jpg"]


def test_publication_rejects_media_that_expired_after_preparation(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = MemoryEbayStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)
    store.media[listing.id] = [
        StoredMedia(
            listing.id,
            1,
            "expired-image",
            "https://img.test/expired.jpg",
            datetime.now(UTC) - timedelta(minutes=1),
        )
    ]

    response = publish_listing(client, listing)

    assert response.status_code == 409
    assert response.json()["code"] == "ebay_photos_expired"
    assert "Try publishing again" in response.json()["detail"]
    assert trading.calls == []
    recovered = store.get_listing(user.id, listing.id)
    assert recovered is not None
    assert recovered.state == "ready"
    assert recovered.publication_attempted is False


def test_uncertain_publication_keeps_lease_when_media_expired(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = MemoryEbayStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)
    publication_lease = store.begin_listing_publication(listing.id)
    store.mark_listing_publication_attempted(listing.id, publication_lease)
    interrupted = store.get_listing(user.id, listing.id)
    assert interrupted is not None
    store.listings[listing.id] = StoredListing(
        **{
            **interrupted.__dict__,
            "publication_started_at": datetime.now(UTC) - timedelta(minutes=16),
        }
    )
    store.media[listing.id] = [
        StoredMedia(
            listing.id,
            1,
            "expired-image",
            "https://img.test/expired.jpg",
            datetime.now(UTC) - timedelta(minutes=1),
        )
    ]

    response = publish_listing(client, listing)

    assert response.status_code == 409
    assert response.json()["code"] == "ebay_photos_expired_recovery"
    assert "Retry after 15 minutes" in response.json()["detail"]
    assert trading.calls == []
    recovered = store.get_listing(user.id, listing.id)
    assert recovered is not None
    assert recovered.state == "publishing"
    assert recovered.publication_attempted is True


def test_live_state_commit_failure_keeps_the_publication_lease(monkeypatch) -> None:
    class CommitFailureStore(MemoryEbayStore):
        def mark_listing_live(self, *_args, **_kwargs) -> None:
            raise EbayStoreUnavailable

    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = CommitFailureStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)

    response = publish_listing(client, listing)

    assert response.status_code == 503
    assert store.get_listing(user.id, listing.id).state == "publishing"


def test_saved_draft_preparation_uses_policies_and_does_not_publish(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    gateway = StubEbayGateway()
    trading = StubTrading()
    client, _, user = authenticated_client(
        ebay_store=store, ebay_gateway=gateway, trading_adapter=trading
    )
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id, 2)

    prepared = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[
            ("photos", ("one.jpg", jpeg_bytes(color="red"), "image/jpeg")),
            ("photos", ("two.jpg", jpeg_bytes(color="blue"), "image/jpeg")),
        ],
    )
    replaced = client.put(
        f"/api/ebay/listings/{draft_id}/photos",
        files=[("photos", ("new.jpg", jpeg_bytes(color="green"), "image/jpeg"))],
    )

    assert prepared.status_code == 201
    assert prepared.json()["photo_count"] == 2
    assert replaced.status_code == 200
    assert replaced.json()["photo_count"] == 1
    listing = store.get_listing(user.id, draft_id)
    assert listing.listing_draft_id == draft_id
    assert listing.draft["business_policies"] == {
        "fulfillment_policy_id": "shipping",
        "payment_policy_id": "payment",
        "return_policy_id": "returns",
    }
    assert listing.draft["package_weight"] == {"pounds": 1, "ounces": 4}
    assert listing.draft["package_dimensions"] == {"length": 12, "width": 8, "height": 3}
    assert listing.draft["item_specifics"][0] == {
        "name": "Brand",
        "values": ["Everlane"],
    }
    assert len(store.list_media(draft_id)) == 1
    assert trading.calls == []


def test_live_preparation_requires_shared_postal_code(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.delenv("EBAY_ITEM_POSTAL_CODE", raising=False)
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)

    response = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 503
    assert response.json()["code"] == "ebay_postal_code_missing"


def test_live_preparation_rejects_a_stale_loaded_draft_revision(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    changed = store.save_draft(
        user.id,
        draft_id,
        listing_draft().model_copy(update={"title": "Newer title"}).model_dump(mode="json"),
        None,
        None,
        1,
    )
    assert changed is not None

    response = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 409
    assert response.json()["code"] == "draft_changed"
    assert "Reload the draft" in response.json()["detail"]
    assert store.get_listing(user.id, draft_id) is None


@pytest.mark.parametrize(
    ("field", "invalid_weight"),
    [
        ("package_weight_ounces", "16"),
        ("package_weight_ounces", "not-a-number"),
        ("package_weight_pounds", "-1"),
        ("package_weight_pounds", "1.5"),
    ],
)
def test_live_preparation_rejects_invalid_package_weight(
    monkeypatch, field: str, invalid_weight: str
) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    data = live_preparation_data(draft_id)
    data[field] = invalid_weight

    response = client.post(
        "/api/ebay/live-listing-preparations",
        data=data,
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_package_weight"
    assert response.json()["detail"] == (
        "Enter a package weight greater than zero. Use 0 to 15 ounces."
    )


@pytest.mark.parametrize("invalid_dimension", ["0", "-1", "11.5", "not-a-number"])
def test_live_preparation_rejects_invalid_package_dimensions(
    monkeypatch, invalid_dimension: str
) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    data = live_preparation_data(draft_id)
    data["package_width"] = invalid_dimension

    response = client.post(
        "/api/ebay/live-listing-preparations",
        data=data,
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_package_dimensions"
    assert response.json()["detail"] == (
        "Enter whole-number package length, width, and height greater than zero."
    )


def test_stale_publication_page_reports_missing_package_dimensions(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    data = live_preparation_data(draft_id)
    for field in ("package_length", "package_width", "package_height"):
        del data[field]

    response = client.post(
        "/api/ebay/live-listing-preparations",
        data=data,
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 422
    assert response.json()["code"] == "stale_publication_page"
    assert response.json()["detail"] == (
        "Reload this page, reopen the saved draft, then enter package length, width, and height."
    )
    assert draft_id not in response.text
    assert "one.jpg" not in response.text


def test_live_preparation_resumes_partial_photo_upload(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    gateway = StubEbayGateway()
    gateway.fail_upload_number = 2
    client, _, user = authenticated_client(ebay_store=store, ebay_gateway=gateway)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id, 2)
    files = [
        ("photos", ("one.jpg", jpeg_bytes(color="red"), "image/jpeg")),
        ("photos", ("two.jpg", jpeg_bytes(color="blue"), "image/jpeg")),
    ]

    failed = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=files,
    )
    gateway.fail_upload_number = None
    resumed = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=files,
    )

    assert failed.status_code == 502
    assert failed.json()["code"] == "ebay_photo_upload_failed"
    assert resumed.status_code == 201
    assert [item.position for item in store.list_media(draft_id)] == [1, 2]
    assert len(gateway.uploads) == 2


def test_live_preparation_replaces_media_after_source_revision_changes(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    gateway = StubEbayGateway()
    client, _, user = authenticated_client(ebay_store=store, ebay_gateway=gateway)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id, 2)
    first_files = [
        ("photos", ("one.jpg", jpeg_bytes(color="red"), "image/jpeg")),
        ("photos", ("two.jpg", jpeg_bytes(color="blue"), "image/jpeg")),
    ]
    replacement_files = [
        ("photos", ("new-one.jpg", jpeg_bytes(color="green"), "image/jpeg")),
        ("photos", ("new-two.jpg", jpeg_bytes(color="yellow"), "image/jpeg")),
    ]

    first = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=first_files,
    )
    saved = store.save_draft(
        user.id,
        draft_id,
        listing_draft().model_copy(update={"title": "Changed photos"}).model_dump(mode="json"),
        DraftEbayCategory(
            category_id="175786", name="Sweaters", path="Women > Sweaters"
        ).model_dump(mode="json"),
        DraftEbayCondition(condition_id=3000, name="Pre-owned").model_dump(mode="json"),
        2,
    )
    assert saved is not None
    replacement_data = live_preparation_data(draft_id)
    replacement_data["draft_revision"] = str(saved.revision)
    replaced = client.post(
        "/api/ebay/live-listing-preparations",
        data=replacement_data,
        files=replacement_files,
    )
    repeated = client.post(
        "/api/ebay/live-listing-preparations",
        data=replacement_data,
        files=replacement_files,
    )

    assert first.status_code == 201
    assert replaced.status_code == 201
    assert repeated.status_code == 201
    assert len(gateway.uploads) == 4
    assert [item.image_id for item in store.list_media(draft_id)] == ["image-3", "image-4"]


def test_live_preparation_blocks_missing_and_invalid_required_aspects(monkeypatch) -> None:
    class RequiredDepartmentGateway(StubEbayGateway):
        def category_requirements(self, category_id):
            requirements = super().category_requirements(category_id)
            return requirements.model_copy(
                update={
                    "aspects": [
                        *requirements.aspects,
                        EbayAspect(
                            name="Department",
                            values=["Women"],
                            required=True,
                            mode="selection",
                            applicable_to=["product"],
                        ),
                    ]
                }
            )

    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(
        ebay_store=store, ebay_gateway=RequiredDepartmentGateway()
    )
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)

    missing = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    saved = store.get_draft(user.id, draft_id)
    draft = listing_draft().model_copy(
        update={
            "item_specifics": [
                *listing_draft().item_specifics,
                ItemSpecific(
                    name="Department",
                    value="Not allowed",
                    source="seller_note",
                    confidence="high",
                ),
            ]
        }
    )
    changed = store.save_draft(
        user.id,
        draft_id,
        draft.model_dump(mode="json"),
        saved.ebay_category,
        saved.ebay_condition,
        1,
    )
    assert changed is not None
    invalid_data = live_preparation_data(draft_id)
    invalid_data["draft_revision"] = str(changed.revision)
    invalid = client.post(
        "/api/ebay/live-listing-preparations",
        data=invalid_data,
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert missing.status_code == 422
    assert "Department" in missing.json()["detail"]
    assert missing.json()["errors"] == [
        {"aspect": "Department", "code": "required", "message": "Enter Department."}
    ]
    assert invalid.status_code == 422
    assert "allowed value" in invalid.json()["detail"]
    assert invalid.json()["errors"] == [
        {
            "aspect": "Department",
            "code": "invalid_value",
            "message": "Choose an allowed value for Department.",
        }
    ]
    assert client.app.state.ebay_gateway.uploads == []


def test_live_preparation_requires_valid_existing_policies(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    data = live_preparation_data(draft_id)
    data["fulfillment_policy_id"] = "not-owned"

    response = client.post(
        "/api/ebay/live-listing-preparations",
        data=data,
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_business_policies"


def test_legacy_handoff_requires_confirmation_before_direct_preparation(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    transfer = seed_historical_transfer(store, user)
    data = live_preparation_data(transfer.listing_id)

    blocked = client.post(
        "/api/ebay/live-listing-preparations",
        data=data,
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    confirmed = client.post(
        "/api/ebay/live-listing-preparations",
        data={**data, "confirm_no_ebay_draft": "true"},
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "legacy_seller_hub_check_required"
    assert confirmed.status_code == 201
    assert store.get_listing_transfer(transfer.listing_id) is None


def test_confirmed_completed_legacy_transfer_stays_blocked(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    transfer = seed_historical_transfer(store, user, "COMPLETED")

    response = client.post(
        "/api/ebay/live-listing-preparations",
        data={**live_preparation_data(transfer.listing_id), "confirm_no_ebay_draft": "true"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "seller_hub_draft_exists"


def test_pending_legacy_transfer_cannot_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    transfer = seed_historical_transfer(store, user, "QUEUED")

    response = client.post(
        "/api/ebay/live-listing-preparations",
        data={**live_preparation_data(transfer.listing_id), "confirm_no_ebay_draft": "true"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "seller_hub_transfer_pending"


def test_successful_publication_retains_the_source_draft(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    prepared = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    published = publish_prepared(client, prepared)

    assert published.status_code == 200
    assert published.json()["item_id"] == "ebay-item-1"
    assert store.get_draft(user.id, draft_id) is not None
    assert store.get_listing(user.id, draft_id).listing_draft_id == draft_id

    reopened = client.get(f"/api/drafts/{draft_id}")
    assert reopened.json()["status"] == "published"
    assert reopened.json()["ebay_item_id"] == "ebay-item-1"
    assert reopened.json()["listing_url"] == "https://www.ebay.com/itm/ebay-item-1"

    update = client.put(
        f"/api/drafts/{draft_id}",
        json={
            "revision": reopened.json()["revision"],
            "draft": listing_draft().model_dump(mode="json"),
            "ebay_category": None,
            "ebay_condition": None,
            "photo_count": 1,
        },
    )
    assert update.status_code == 409
    assert update.json()["code"] == "draft_published"

    deleted = client.delete(f"/api/drafts/{draft_id}")
    assert deleted.status_code == 204
    assert store.get_draft(user.id, draft_id) is None
    live_mapping = store.get_listing(user.id, draft_id)
    assert live_mapping is not None
    assert live_mapping.ebay_item_id == "ebay-item-1"
    assert live_mapping.listing_draft_id is None


def test_preparation_cannot_replace_a_published_listing_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=StubTrading())
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    prepared = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    published = publish_prepared(client, prepared)
    replacement = live_preparation_data(draft_id)
    replacement["package_length"] = "20"

    rejected = client.post(
        "/api/ebay/live-listing-preparations",
        data=replacement,
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert published.status_code == 200
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "draft_changed"
    listing = store.get_listing(user.id, draft_id)
    assert listing is not None
    assert listing.draft["package_dimensions"]["length"] == 12


def test_publication_rechecks_source_version_before_ebay_calls(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    prepared = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    changed = store.save_draft(
        user.id,
        draft_id,
        listing_draft().model_copy(update={"title": "Changed title"}).model_dump(mode="json"),
        None,
        None,
        1,
    )
    assert changed is not None

    published = publish_prepared(client, prepared)

    assert published.status_code == 409
    assert published.json()["code"] == "draft_changed"
    assert trading.calls == []
    assert store.get_listing(user.id, draft_id).state == "ready"


def test_publication_rejects_a_superseded_preparation_revision(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    first = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    saved = store.get_draft(user.id, draft_id)
    assert saved is not None
    changed = store.save_draft(
        user.id,
        draft_id,
        listing_draft().model_copy(update={"title": "Newer title"}).model_dump(mode="json"),
        saved.ebay_category,
        saved.ebay_condition,
        1,
    )
    assert changed is not None
    second_data = live_preparation_data(draft_id)
    second_data["draft_revision"] = str(changed.revision)
    second = client.post(
        "/api/ebay/live-listing-preparations",
        data=second_data,
        files=[("photos", ("newer.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    published = publish_prepared(client, first)

    assert first.status_code == 201
    assert second.status_code == 201
    assert published.status_code == 409
    assert published.json()["code"] == "draft_changed"
    assert trading.calls == []
    assert store.get_listing(user.id, draft_id).state == "ready"


def test_publication_rejects_a_superseded_same_revision_preparation(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    gateway = StubEbayGateway()
    trading = StubTrading()
    policies = gateway.business_policies("access-token")
    policies.append({"type": "fulfillment_policy", "id": "express", "name": "Express shipping"})
    monkeypatch.setattr(gateway, "business_policies", lambda _token: policies)
    client, _, user = authenticated_client(
        ebay_store=store,
        ebay_gateway=gateway,
        trading_adapter=trading,
    )
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    first = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    second_data = live_preparation_data(draft_id)
    second_data["fulfillment_policy_id"] = "express"
    second = client.post(
        "/api/ebay/live-listing-preparations",
        data=second_data,
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    published = publish_prepared(client, first)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["draft_revision"] == second.json()["draft_revision"]
    assert first.json()["preparation_id"] != second.json()["preparation_id"]
    assert published.status_code == 409
    assert published.json()["code"] == "draft_changed"
    assert trading.calls == []


def test_draft_deletion_is_rejected_while_publication_runs(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    prepared = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    store.begin_listing_publication(prepared.json()["listing_id"])

    deleted = client.delete(f"/api/drafts/{draft_id}")

    assert deleted.status_code == 409
    assert deleted.json()["code"] == "draft_publication_in_progress"
    assert store.get_draft(user.id, draft_id) is not None
    assert store.get_listing(user.id, draft_id) is not None
    assert store.list_media(draft_id)


def test_stale_publication_record_recovers_with_original_preparation(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    gateway = StubEbayGateway()
    trading = StubTrading()
    client, _, user = authenticated_client(
        ebay_store=store,
        ebay_gateway=gateway,
        trading_adapter=trading,
    )
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    first = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    listing = store.get_listing(user.id, draft_id)
    assert listing is not None
    store.begin_listing_publication(listing.id)
    interrupted = store.get_listing(user.id, draft_id)
    assert interrupted is not None
    store.listings[draft_id] = StoredListing(
        **{
            **interrupted.__dict__,
            "publication_started_at": datetime.now(UTC) - timedelta(minutes=16),
        }
    )

    recovery_data = live_preparation_data(draft_id)
    recovery_data["package_length"] = "20"
    rejected = client.post(
        "/api/ebay/live-listing-preparations",
        data=recovery_data,
        files=[("photos", ("replacement.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    monkeypatch.setattr(
        gateway,
        "category_requirements",
        lambda _category_id: pytest.fail("recovery revalidated category metadata"),
    )
    monkeypatch.setattr(
        gateway,
        "business_policies",
        lambda _token: pytest.fail("recovery revalidated business policies"),
    )
    recovery_data["recovery_preparation_id"] = first.json()["preparation_id"]
    recovery_data["fulfillment_policy_id"] = "removed-shipping-policy"
    recovery_data["package_length"] = "invalid-current-package-length"
    recovered = client.post(
        "/api/ebay/live-listing-preparations",
        data=recovery_data,
        files=[("photos", ("replacement.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    published = publish_prepared(client, recovered)

    assert first.status_code == 201
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "draft_changed"
    assert recovered.status_code == 201
    assert recovered.json()["listing_id"] == draft_id
    assert published.status_code == 200
    assert published.json()["item_id"] == "ebay-item-1"
    final_listing = store.get_listing(user.id, draft_id)
    assert final_listing is not None
    assert final_listing.state == "live"
    assert final_listing.draft["package_dimensions"]["length"] == 12


def test_stale_publication_refreshes_expired_media_from_original_photos(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = MemoryEbayStore()
    gateway = StubEbayGateway()
    client, _, user = authenticated_client(ebay_store=store, ebay_gateway=gateway)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)
    first = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    publication_lease = store.begin_listing_publication(draft_id)
    store.mark_listing_publication_attempted(draft_id, publication_lease)
    interrupted = store.get_listing(user.id, draft_id)
    assert interrupted is not None
    store.listings[draft_id] = StoredListing(
        **{
            **interrupted.__dict__,
            "publication_started_at": datetime.now(UTC) - timedelta(minutes=16),
        }
    )
    store.media[draft_id] = [
        StoredMedia(
            draft_id,
            1,
            "expired-image",
            "https://img.test/expired.jpg",
            datetime.now(UTC) - timedelta(minutes=1),
        )
    ]

    recovery_data = live_preparation_data(draft_id)
    recovery_data["recovery_preparation_id"] = first.json()["preparation_id"]
    recovered = client.post(
        "/api/ebay/live-listing-preparations",
        data=recovery_data,
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert first.status_code == 201
    assert recovered.status_code == 201
    assert len(gateway.uploads) == 2
    refreshed = store.list_media(draft_id)
    assert refreshed[0].image_id == "image-2"
    assert refreshed[0].expires_at > datetime.now(UTC)


def test_publication_preparation_rejects_a_concurrent_draft_change(monkeypatch) -> None:
    class ChangedDraftStore(MemoryEbayStore):
        def get_or_create_listing(self, *_args, **_kwargs):
            raise DraftChangedDuringPublication

    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    store = ChangedDraftStore()
    client, _, user = authenticated_client(ebay_store=store)
    connect_store(client, store, user)
    draft_id = str(uuid.uuid4())
    save_transfer_draft(store, user, draft_id)

    response = client.post(
        "/api/ebay/live-listing-preparations",
        data=live_preparation_data(draft_id),
        files=[("photos", ("one.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 409
    assert response.json()["code"] == "draft_changed"


def test_publish_refuses_listing_already_sent_to_seller_hub(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = MemoryEbayStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)
    store.save_transfer(
        StoredTransfer(
            str(uuid.uuid4()),
            listing.id,
            user.id,
            "task-1",
            "QUEUED",
            None,
            datetime.now(UTC),
        )
    )

    response = publish_listing(client, listing)

    assert response.status_code == 409
    assert response.json()["code"] == "seller_hub_draft_exists"
    assert trading.calls == []


def test_revision_conflict_refreshes_instead_of_overwriting(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = MemoryEbayStore()
    trading = StubTrading()
    trading.current_revision = "seller-hub-change"
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)
    publication_lease = store.begin_listing_publication(listing.id)
    store.mark_listing_live(
        listing.id,
        "ebay-item-1",
        "old-revision",
        publication_lease=publication_lease,
    )

    response = client.patch(
        f"/api/ebay/listings/{listing.id}",
        json={
            "expected_revision": "old-revision",
            "listing": live_listing().model_dump(mode="json"),
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "ebay_listing_changed"
    assert trading.calls == ["get"]


def test_live_listing_can_refresh_revise_end_and_relist(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    store = MemoryEbayStore()
    trading = StubTrading()
    client, _, user = authenticated_client(ebay_store=store, trading_adapter=trading)
    connect_store(client, store, user)
    listing = seed_live_ready_listing(client, store, user)
    publication_lease = store.begin_listing_publication(listing.id)
    store.mark_listing_live(
        listing.id,
        "ebay-item-1",
        "revision-1",
        publication_lease=publication_lease,
    )
    trading.current_listing = live_listing().model_copy(
        update={"title": "Title changed in Seller Hub", "price": 33}
    )
    trading.current_image_urls = ["https://img.test/seller-hub-photo.jpg"]

    refreshed = client.get(f"/api/ebay/listings/{listing.id}")
    revised = client.patch(
        f"/api/ebay/listings/{listing.id}",
        json={
            "expected_revision": "revision-1",
            "listing": live_listing().model_copy(update={"price": 34}).model_dump(mode="json"),
        },
    )
    ended = client.post(f"/api/ebay/listings/{listing.id}/end", json={"reason": "NotAvailable"})
    relisted = client.post(f"/api/ebay/listings/{listing.id}/relist")

    assert refreshed.status_code == 200
    assert refreshed.json()["listing"]["title"] == "Title changed in Seller Hub"
    assert refreshed.json()["photos"] == ["https://img.test/seller-hub-photo.jpg"]
    assert revised.status_code == 200
    assert ended.status_code == 200
    assert relisted.status_code == 200
    assert store.get_listing(user.id, listing.id).ebay_item_id == "ebay-item-2"
    assert trading.calls == ["get", "get", "revise", "end", "get", "relist"]
