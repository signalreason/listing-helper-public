import json
import logging
import uuid
from io import BytesIO

from PIL import Image

from app.ebay_store import EbayStoreUnavailable, MemoryEbayStore
from app.generation import GenerationFailed
from app.images import MAX_FILE_BYTES, MAX_PHOTOS
from tests.support import GENERATION_CATEGORY, StubGenerator, authenticated_client, jpeg_bytes


def test_page_is_served_without_reading_openai_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client, _, _ = authenticated_client()

    response = client.get("/")

    assert response.status_code == 200
    assert "Add photos. Get a listing draft." in response.text
    assert "OPENAI_API_KEY" not in response.text


def test_browser_pages_and_assets_disable_heuristic_caching() -> None:
    client, _, _ = authenticated_client()
    for path in (
        "/",
        "/login",
        "/static/index.html",
        "/static/app.js?v=20260906-1",
        "/static/photo-store.js?v=20260906-1",
        "/static/styles.css?v=20260906-1",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        if path.startswith("/static/"):
            revalidated = client.get(path, headers={"If-None-Match": response.headers["etag"]})
            assert revalidated.status_code == 304
            assert revalidated.headers["cache-control"] == "no-store"


def test_valid_upload_returns_structured_draft_and_normalizes_photo() -> None:
    generator = StubGenerator()
    client, _, _ = authenticated_client(generator)

    response = client.post(
        "/api/listings/generate",
        files=[("photos", ("../../sweater.jpg", jpeg_bytes(), "image/jpeg"))],
        data={
            "ebay_category": json.dumps(GENERATION_CATEGORY),
            **{"notes": "Small snag near the left cuff."},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["draft"]["title"].startswith("Everlane")
    assert payload["draft"]["pricing"]["currency"] == "USD"
    assert payload["saved"] is True
    assert payload["draft_revision"] == 1
    assert client.app.state.ebay_store.get_draft(
        client.app.state.accounts.list_users()[0].id, payload["draft_id"]
    )
    assert generator.calls == [(1, "Small snag near the left cuff.")]
    assert [path.name for path in generator.paths] == ["normalized-1.jpg"]
    assert all(not path.exists() for path in generator.paths)


def test_saved_draft_can_be_edited_reopened_listed_and_deleted() -> None:
    client, _, user = authenticated_client()
    generated = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files=[("photos", ("item.jpg", jpeg_bytes(), "image/jpeg"))],
    ).json()
    draft_id = generated["draft_id"]
    edited = generated["draft"]
    edited["title"] = "Saved edited title"
    edited["package_weight"] = {"pounds": 1, "ounces": 4}
    edited["package_dimensions"] = {"length": 12, "width": 8, "height": 3}

    saved = client.put(
        f"/api/drafts/{draft_id}",
        json={
            "revision": generated["draft_revision"],
            "draft": edited,
            "ebay_category": {
                "category_id": "175786",
                "name": "Sweaters",
                "path": "Women > Sweaters",
            },
            "ebay_condition": {"condition_id": 3000, "name": "Pre-owned"},
            "photo_count": 1,
        },
    )
    reopened = client.get(f"/api/drafts/{draft_id}")
    listed = client.get("/api/drafts")

    assert saved.status_code == 200
    assert saved.json()["revision"] == generated["draft_revision"] + 1
    assert reopened.json()["revision"] == saved.json()["revision"]
    assert reopened.json()["draft"]["title"] == "Saved edited title"
    assert reopened.json()["draft"]["package_weight"] == {"pounds": 1, "ounces": 4}
    assert reopened.json()["draft"]["package_dimensions"] == {
        "length": 12,
        "width": 8,
        "height": 3,
    }
    assert listed.json()["drafts"][0]["id"] == draft_id
    assert listed.json()["drafts"][0]["title"] == "Saved edited title"

    stale_draft = {**edited, "title": "Stale title"}
    stale = client.put(
        f"/api/drafts/{draft_id}",
        json={
            "revision": generated["draft_revision"],
            "draft": stale_draft,
            "ebay_category": None,
            "ebay_condition": None,
            "photo_count": 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "draft_changed"
    assert client.get(f"/api/drafts/{draft_id}").json()["draft"]["title"] == "Saved edited title"

    deleted = client.delete(f"/api/drafts/{draft_id}")
    assert deleted.status_code == 204
    assert client.app.state.ebay_store.get_draft(user.id, draft_id) is None


def test_saved_draft_list_fetches_publication_state_in_one_batch() -> None:
    class CountingStore(MemoryEbayStore):
        def __init__(self) -> None:
            super().__init__()
            self.batch_calls = 0
            self.single_calls = 0

        def get_draft_listings(self, user_id: int, draft_ids: list[str]):
            self.batch_calls += 1
            return super().get_draft_listings(user_id, draft_ids)

        def get_listing(self, user_id: int, listing_id: str):
            self.single_calls += 1
            return super().get_listing(user_id, listing_id)

    store = CountingStore()
    client, _, user = authenticated_client(ebay_store=store)
    for _ in range(3):
        store.save_draft(
            user.id,
            str(uuid.uuid4()),
            StubGenerator().draft.model_dump(mode="json"),
            None,
            None,
            1,
        )

    response = client.get("/api/drafts")

    assert response.status_code == 200
    assert len(response.json()["drafts"]) == 3
    assert store.batch_calls == 1
    assert store.single_calls == 0


def test_legacy_item_specific_value_converts_on_load_and_saves_as_values() -> None:
    store = MemoryEbayStore()
    client, _, user = authenticated_client(ebay_store=store)
    draft_id = str(uuid.uuid4())
    legacy = StubGenerator().draft.model_dump(mode="json")
    legacy["item_specifics"] = [
        {
            "name": "Brand",
            "value": "Everlane",
            "source": "photo",
            "confidence": "high",
        }
    ]
    store.save_draft(user.id, draft_id, legacy, None, None, 1)

    reopened = client.get(f"/api/drafts/{draft_id}")
    payload = reopened.json()

    assert reopened.status_code == 200
    assert payload["draft"]["item_specifics"] == [
        {
            "name": "Brand",
            "values": ["Everlane"],
            "source": "photo",
            "confidence": "high",
        }
    ]
    assert "value" in store.get_draft(user.id, draft_id).draft["item_specifics"][0]

    saved = client.put(
        f"/api/drafts/{draft_id}",
        json={
            "revision": payload["revision"],
            "draft": payload["draft"],
            "ebay_category": None,
            "ebay_condition": None,
            "photo_count": 1,
        },
    )

    assert saved.status_code == 200
    stored_specific = store.get_draft(user.id, draft_id).draft["item_specifics"][0]
    assert stored_specific["values"] == ["Everlane"]
    assert "value" not in stored_specific


def test_saved_drafts_are_scoped_to_the_authenticated_user() -> None:
    store = MemoryEbayStore()
    client, _, _ = authenticated_client(ebay_store=store)
    other_id = str(uuid.uuid4())
    store.save_draft(999, other_id, StubGenerator().draft.model_dump(), None, None, 1)

    assert client.get(f"/api/drafts/{other_id}").status_code == 404
    assert client.delete(f"/api/drafts/{other_id}").status_code == 404
    assert client.get("/api/drafts").json() == {"drafts": []}


def test_generated_data_is_returned_when_draft_storage_is_temporarily_unavailable(
    caplog,
) -> None:
    class UnavailableDraftStore(MemoryEbayStore):
        def save_draft(self, *args, **kwargs):
            raise EbayStoreUnavailable(
                "private database failure",
                error_class="schema",
                sqlstate="42P01",
            )

    client, _, _ = authenticated_client(ebay_store=UnavailableDraftStore())

    with caplog.at_level(logging.WARNING, logger="app.draft_store"):
        response = client.post(
            "/api/listings/generate",
            data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
            files=[("photos", ("item.jpg", jpeg_bytes(), "image/jpeg"))],
        )

    assert response.status_code == 200
    assert response.json()["saved"] is False
    assert response.json()["ebay_category"] == GENERATION_CATEGORY
    assert response.json()["draft_revision"] is None
    assert response.json()["draft"]["title"]
    assert "operation=initial_save" in caplog.text
    assert "outcome=failure" in caplog.text
    assert "error_class=schema" in caplog.text
    assert "sqlstate=42P01" in caplog.text
    assert "private database failure" not in caplog.text


def test_draft_save_retry_has_recovery_instructions_and_safe_log(caplog) -> None:
    class UnavailableDraftStore(MemoryEbayStore):
        def save_draft(self, *args, **kwargs):
            raise EbayStoreUnavailable(
                "private connection details",
                error_class="connection",
            )

    client, _, _ = authenticated_client(ebay_store=UnavailableDraftStore())
    generated = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files=[("photos", ("item.jpg", jpeg_bytes(), "image/jpeg"))],
    ).json()

    with caplog.at_level(logging.WARNING, logger="app.draft_store"):
        response = client.put(
            f"/api/drafts/{generated['draft_id']}",
            json={
                "revision": generated["draft_revision"],
                "draft": generated["draft"],
                "ebay_category": None,
                "ebay_condition": None,
                "photo_count": 1,
            },
        )

    assert response.status_code == 503
    assert response.json()["code"] == "draft_save_unavailable"
    assert response.json()["detail"] == (
        "We could not save this draft. Keep this page open and try Save now again."
    )
    assert "operation=retry_save" in caplog.text
    assert "error_class=connection" in caplog.text
    assert "private connection details" not in caplog.text


def test_healthcheck_includes_listing_store_and_logs_safe_failure(caplog) -> None:
    class UnavailableHealthStore(MemoryEbayStore):
        def healthcheck(self):
            raise EbayStoreUnavailable(
                "private database host",
                error_class="connection",
                sqlstate="08006",
            )

    client, _, _ = authenticated_client(ebay_store=UnavailableHealthStore())

    with caplog.at_level(logging.WARNING, logger="app.health"):
        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "component=listing_store" in caplog.text
    assert "error_class=connection" in caplog.text
    assert "sqlstate=08006" in caplog.text
    assert "private database host" not in caplog.text


def test_iphone_multi_picture_jpeg_is_accepted_and_normalized() -> None:
    buffer = BytesIO()
    primary = Image.new("RGB", (64, 64), "black")
    depth_or_secondary = Image.new("RGB", (64, 64), "white")
    primary.save(buffer, format="MPO", save_all=True, append_images=[depth_or_secondary])
    primary.close()
    depth_or_secondary.close()

    generator = StubGenerator()
    client, _, _ = authenticated_client(generator)
    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files=[("photos", ("IMG_1234.jpeg", buffer.getvalue(), "image/jpeg"))],
    )

    assert response.status_code == 200
    assert generator.calls == [(1, "")]


def test_exact_12_mb_boundary_is_accepted() -> None:
    generator = StubGenerator()
    client, _, _ = authenticated_client(generator)
    image = jpeg_bytes()
    padded = image + b"\0" * (MAX_FILE_BYTES - len(image))

    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files=[("photos", ("boundary.jpg", padded, "image/jpeg"))],
    )

    assert response.status_code == 200
    assert generator.calls == [(1, "")]


def test_requires_at_least_one_photo() -> None:
    client, _, _ = authenticated_client()
    response = client.post(
        "/api/listings/generate", data={"ebay_category": json.dumps(GENERATION_CATEGORY)}
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "photos_required"
    assert response.json()["detail"] == "Choose at least one photo."


def test_rejects_more_than_ebay_photo_count_limit() -> None:
    photo = jpeg_bytes(32, 32)
    files = [
        ("photos", (f"photo-{index}.jpg", photo, "image/jpeg")) for index in range(MAX_PHOTOS + 1)
    ]

    client, _, _ = authenticated_client()
    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files=files,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "too_many_photos"


def test_rejects_photo_over_12_mb() -> None:
    client, _, _ = authenticated_client()
    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files=[("photos", ("large.jpg", b"x" * (MAX_FILE_BYTES + 1), "image/jpeg"))],
    )

    assert response.status_code == 413
    assert response.json()["code"] == "photo_too_large"
    assert response.json()["detail"] == "Each photo must be 12 MB or smaller."


def test_rejects_file_that_is_not_a_supported_image() -> None:
    client, _, _ = authenticated_client()
    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files=[("photos", ("fake.jpg", b"plain text", "image/jpeg"))],
    )

    assert response.status_code == 415
    assert response.json()["code"] == "invalid_photo"


def test_missing_key_returns_generic_error_without_exposing_environment(monkeypatch) -> None:
    secret = "secret-that-must-not-escape"
    monkeypatch.setenv("UNRELATED_SECRET", secret)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client, _, _ = authenticated_client(generator=None)
    client.app.state.generator = __import__(
        "app.generation", fromlist=["OpenAIListingGenerator"]
    ).OpenAIListingGenerator()
    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files=[("photos", ("item.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 503
    assert response.json()["code"] == "generation_unavailable"
    assert secret not in response.text


def test_generation_failure_is_recoverable_and_safe() -> None:
    class FailingGenerator:
        def generate(self, _images, _notes, _category, _requirements):
            raise GenerationFailed("sensitive provider detail")

    client, _, _ = authenticated_client(FailingGenerator())
    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files=[("photos", ("item.jpg", jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 502
    assert "photos are still selected" in response.json()["detail"]
    assert "sensitive provider detail" not in response.text
