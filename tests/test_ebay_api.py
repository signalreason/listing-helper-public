import csv
import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import httpx
import pytest

from app.ebay_api import (
    DRAFT_FEED_ACTION_HEADER,
    DRAFT_FEED_TEMPLATE,
    DRAFT_FEED_TEMPLATE_VERSION,
    OAUTH_SCOPES,
    EbayApiError,
    EbayHttpGateway,
    build_draft_feed,
    draft_feed_filename,
)
from app.ebay_models import EbayItemSpecific, EbayReadyListing


def ebay_listing() -> EbayReadyListing:
    return EbayReadyListing(
        title="Green cotton sweater",
        category_id="175786",
        category_name="Sweaters",
        condition_id=3000,
        condition_description="Good used condition.",
        description="Green sweater in a medium.",
        item_specifics=[
            EbayItemSpecific(name="Brand", value="Everlane"),
            EbayItemSpecific(name="Size", value="M"),
        ],
        price=32,
    )


def test_draft_feed_filename_matches_successful_ebay_template_pattern() -> None:
    assert draft_feed_filename(datetime(2026, 8, 17, 9, 10, tzinfo=UTC)) == (
        "eBay-draft-listing-template-Aug-17-2026-09-10-00.csv"
    )


def test_draft_feed_contains_one_draft_with_ordered_photos() -> None:
    urls = [f"https://i.ebayimg.test/{number}.jpg" for number in range(1, 25)]

    data = build_draft_feed(ebay_listing(), "LA-1-abc", urls)
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    assert parsed[:5] == [
        [
            "#INFO",
            f"Version={DRAFT_FEED_TEMPLATE_VERSION}",
            f"Template= {DRAFT_FEED_TEMPLATE}",
            *([""] * 8),
        ],
        [
            "#INFO Action and Category ID are required fields. 1) Set Action to Draft "
            "2) Please find the category ID for your listings here: "
            "https://pages.ebay.com/sellerinformation/news/categorychanges.html",
            *([""] * 10),
        ],
        [
            "#INFO After you've successfully uploaded your draft from the Seller Hub "
            "Reports tab, complete your drafts to active listings here: "
            "https://www.ebay.com/sh/lst/drafts",
            *([""] * 10),
        ],
        ["#INFO", *([""] * 10)],
        [
            DRAFT_FEED_ACTION_HEADER,
            "Custom label (SKU)",
            "Category ID",
            "Title",
            "UPC",
            "Price",
            "Quantity",
            "Item photo URL",
            "Condition ID",
            "Description",
            "Format",
        ],
    ]
    assert len(parsed) == 6
    row = dict(zip(parsed[4], parsed[5], strict=True))
    assert row[DRAFT_FEED_ACTION_HEADER] == "Draft"
    assert row["Format"] == "FixedPrice"
    assert row["Price"] == "32.00"
    assert row["Quantity"] == "1"
    assert row["Custom label (SKU)"] == ""
    assert row["Category ID"] == "175786"
    assert row["Condition ID"] == "USED"
    assert row["Item photo URL"].split("|") == urls
    assert row["Description"].endswith("<br><br>Item specifics:<br>Brand: Everlane<br>Size: M")
    assert all(not field.startswith("C:") for field in parsed[4])
    assert "Duration" not in parsed[4]
    assert "Test Draft Shoe" not in data.decode("utf-8")


def test_draft_feed_omits_complete_photo_url_that_contains_tilde() -> None:
    urls = [
        "https://i.ebayimg.test/one.jpg",
        "https://i.ebayimg.test/a~b/two.jpg",
        "https://i.ebayimg.test/three.jpg",
    ]

    data = build_draft_feed(ebay_listing(), "LA-1-abc", urls)
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    row = dict(zip(parsed[4], parsed[5], strict=True))

    assert row["Item photo URL"].split("|") == [urls[0], urls[2]]
    assert urls[1] not in data.decode("utf-8")


def test_draft_feed_uses_parser_safe_bytes_and_html_line_breaks() -> None:
    listing = ebay_listing().model_copy(
        update={
            "condition_id": 1000,
            "description": "First line.\r\nSecond line.\nThird line.",
        }
    )

    data = build_draft_feed(listing, "LA-1-abc", ["https://i.ebayimg.test/1.jpg"])

    assert data.startswith(
        b"#INFO,Version=0.0.2,Template= eBay-draft-listings-template_US,,,,,,,,\r\n"
    )
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.count(b"\r\n") == 6
    assert b"\n" not in data.replace(b"\r\n", b"")
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    row = dict(zip(parsed[4], parsed[5], strict=True))
    assert row["Condition ID"] == "NEW"
    assert row["Description"].startswith("First line.<br>Second line.<br>Third line.")


def test_media_upload_reads_id_from_location_and_current_expiry_field(
    monkeypatch, tmp_path
) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"jpeg")

    def fake_post(url, **kwargs):
        assert kwargs["files"]["image"][0] == "image.jpg"
        return httpx.Response(
            201,
            headers={"location": "https://apim.ebay.com/commerce/media/v1_beta/image/image-123"},
            json={
                "imageUrl": "https://i.ebayimg.com/image-123.jpg",
                "expirationDate": "2026-09-01T12:00:00.000Z",
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    gateway = EbayHttpGateway(environment="production")

    result = gateway.upload_image("token", image_path)

    assert result.image_id == "image-123"
    assert result.image_url == "https://i.ebayimg.com/image-123.jpg"
    assert result.expires_at == datetime.fromisoformat("2026-09-01T12:00:00+00:00")


def test_identity_uses_oauth_token_introspection(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_CLIENT_ID", "client-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("EBAY_RUNAME", "redirect-name")

    def fake_post(url, **kwargs):
        assert url == "https://api.ebay.com/identity/v1/oauth2/token/introspect"
        assert kwargs["data"] == {
            "token": "user-access-token",
            "token_type_hint": "access_token",
        }
        return httpx.Response(
            200,
            json={"active": True, "sub": "immutable-ebay-user", "username": "seller-name"},
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    identity = EbayHttpGateway(environment="production").identity("user-access-token")

    assert identity.user_id == "immutable-ebay-user"
    assert identity.display_name == "seller-name"
    assert "commerce.identity.readonly" not in OAUTH_SCOPES


def _configure_ebay_credentials(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_CLIENT_ID", "client-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("EBAY_RUNAME", "redirect-name")


def test_application_token_reuses_then_refreshes_from_expires_in(monkeypatch) -> None:
    _configure_ebay_credentials(monkeypatch)
    now = [1_000.0]
    token_calls = []
    taxonomy_tokens = []

    def fake_post(_url, **_kwargs):
        token_calls.append(len(token_calls) + 1)
        return httpx.Response(
            200,
            json={"access_token": f"token-{len(token_calls)}", "expires_in": 7_200},
        )

    def fake_get(_url, **kwargs):
        taxonomy_tokens.append(kwargs["headers"]["Authorization"])
        return httpx.Response(200, json={"categorySuggestions": []})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    gateway = EbayHttpGateway(environment="production", clock=lambda: now[0])

    gateway.category_suggestions("sweater")
    now[0] += 7_139
    gateway.category_suggestions("shirt")
    now[0] += 1
    gateway.category_suggestions("jacket")

    assert token_calls == [1, 2]
    assert taxonomy_tokens == ["Bearer token-1", "Bearer token-1", "Bearer token-2"]


def test_application_token_refresh_is_single_flight(monkeypatch) -> None:
    _configure_ebay_credentials(monkeypatch)
    now = [1_000.0]
    token_calls = []

    def fake_post(_url, **_kwargs):
        token_calls.append(len(token_calls) + 1)
        return httpx.Response(
            200,
            json={"access_token": f"token-{len(token_calls)}", "expires_in": 7_200},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    gateway = EbayHttpGateway(environment="production", clock=lambda: now[0])
    assert gateway._application_access_token() == "token-1"
    now[0] += 7_140
    barrier = Barrier(8)

    def get_token(_number):
        barrier.wait()
        return gateway._application_access_token()

    with ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(get_token, range(8)))

    assert tokens == ["token-2"] * 8
    assert token_calls == [1, 2]


def test_category_suggestions_retry_cached_token_401_once_with_safe_log(
    monkeypatch, caplog
) -> None:
    _configure_ebay_credentials(monkeypatch)
    token_calls = []
    taxonomy_tokens = []

    def fake_post(_url, **_kwargs):
        token_calls.append(len(token_calls) + 1)
        return httpx.Response(
            200,
            json={
                "access_token": f"private-token-{len(token_calls)}",
                "expires_in": 7_200,
            },
        )

    def fake_get(_url, **kwargs):
        taxonomy_tokens.append(kwargs["headers"]["Authorization"])
        if len(taxonomy_tokens) == 1:
            return httpx.Response(401, text="private listing response body")
        return httpx.Response(
            200,
            json={
                "categorySuggestions": [
                    {
                        "category": {"categoryId": "175786", "categoryName": "Sweaters"},
                        "categoryTreeNodeAncestors": [],
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    gateway = EbayHttpGateway(environment="production")
    assert gateway._application_access_token() == "private-token-1"

    with caplog.at_level(logging.INFO, logger="app.ebay.category"):
        suggestions = gateway.category_suggestions("private search query")

    assert suggestions[0].category_id == "175786"
    assert token_calls == [1, 2]
    assert taxonomy_tokens == ["Bearer private-token-1", "Bearer private-token-2"]
    assert "operation=category_suggestions" in caplog.text
    assert "failure_class=none" in caplog.text
    assert "provider_status=200" in caplog.text
    assert "attempt_count=2" in caplog.text
    assert "outcome=success" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "private-token" not in caplog.text
    assert "private search query" not in caplog.text
    assert "private listing response body" not in caplog.text
    assert re.fullmatch(
        r"operation=category_suggestions failure_class=none provider_status=200 "
        r"attempt_count=2 outcome=success duration_ms=\d+",
        caplog.messages[-1],
    )


def test_category_requirements_retry_uses_one_new_token_for_both_gets(monkeypatch) -> None:
    _configure_ebay_credentials(monkeypatch)
    token_calls = []
    taxonomy_tokens = []

    def fake_post(_url, **_kwargs):
        token_calls.append(len(token_calls) + 1)
        return httpx.Response(
            200,
            json={"access_token": f"token-{len(token_calls)}", "expires_in": 7_200},
        )

    def fake_get(url, **kwargs):
        taxonomy_tokens.append(kwargs["headers"]["Authorization"])
        if len(taxonomy_tokens) == 1:
            return httpx.Response(401)
        if "get_item_aspects_for_category" in url:
            return httpx.Response(200, json={"aspects": []})
        return httpx.Response(200, json={"itemConditionPolicies": []})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    gateway = EbayHttpGateway(environment="production")
    assert gateway._application_access_token() == "token-1"

    result = gateway.category_requirements("3001")

    assert result.category.category_id == "3001"
    assert token_calls == [1, 2]
    assert taxonomy_tokens == ["Bearer token-1", "Bearer token-2", "Bearer token-2"]


def test_category_401_retry_exhaustion_is_safe_and_does_not_expire_seller_auth(
    monkeypatch, caplog
) -> None:
    _configure_ebay_credentials(monkeypatch)
    token_calls = []
    get_calls = []

    def fake_post(_url, **_kwargs):
        token_calls.append(len(token_calls) + 1)
        return httpx.Response(
            200,
            json={"access_token": f"secret-token-{len(token_calls)}", "expires_in": 7_200},
        )

    def fake_get(_url, **_kwargs):
        get_calls.append(1)
        return httpx.Response(401, text="private provider response")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    gateway = EbayHttpGateway(environment="production")
    gateway._application_access_token()

    with (
        caplog.at_level(logging.INFO, logger="app.ebay.category"),
        pytest.raises(EbayApiError) as caught,
    ):
        gateway.category_suggestions("secret query")

    assert caught.value.authorization_expired is False
    assert caught.value.failure_class == "authorization"
    assert caught.value.provider_status == 401
    assert token_calls == [1, 2]
    assert get_calls == [1, 1]
    assert "failure_class=authorization" in caplog.text
    assert "provider_status=401" in caplog.text
    assert "attempt_count=2" in caplog.text
    assert "outcome=failure" in caplog.text
    assert "secret-token" not in caplog.text
    assert "secret query" not in caplog.text
    assert "private provider response" not in caplog.text
    assert re.fullmatch(
        r"operation=category_suggestions failure_class=authorization provider_status=401 "
        r"attempt_count=2 outcome=failure duration_ms=\d+",
        caplog.messages[-1],
    )


def test_non_401_category_failure_is_not_retried(monkeypatch, caplog) -> None:
    _configure_ebay_credentials(monkeypatch)
    token_calls = []
    get_calls = []

    def fake_post(_url, **_kwargs):
        token_calls.append(1)
        return httpx.Response(200, json={"access_token": "application-token", "expires_in": 7_200})

    def fake_get(_url, **_kwargs):
        get_calls.append(1)
        return httpx.Response(503, text="private provider response")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    gateway = EbayHttpGateway(environment="production")
    gateway._application_access_token()

    with (
        caplog.at_level(logging.INFO, logger="app.ebay.category"),
        pytest.raises(EbayApiError) as caught,
    ):
        gateway.category_suggestions("private query")

    assert caught.value.provider_status == 503
    assert token_calls == [1]
    assert get_calls == [1]
    assert "failure_class=provider" in caplog.text
    assert "provider_status=503" in caplog.text
    assert "attempt_count=1" in caplog.text
    assert "outcome=failure" in caplog.text
    assert "private query" not in caplog.text
    assert "private provider response" not in caplog.text


def test_application_token_failure_logs_safe_provider_status(monkeypatch, caplog) -> None:
    _configure_ebay_credentials(monkeypatch)

    def fake_post(_url, **_kwargs):
        return httpx.Response(
            503,
            text=(
                "private provider body with secret-token, seller@example.com, "
                "and Private Listing Title"
            ),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    gateway = EbayHttpGateway(environment="production")

    with (
        caplog.at_level(logging.INFO, logger="app.ebay.category"),
        pytest.raises(EbayApiError) as caught,
    ):
        gateway.category_suggestions("private category query")

    assert caught.value.authorization_expired is False
    assert caught.value.failure_class == "authorization"
    assert caught.value.provider_status == 503
    assert re.fullmatch(
        r"operation=category_suggestions failure_class=authorization provider_status=503 "
        r"attempt_count=1 outcome=failure duration_ms=\d+",
        caplog.messages[-1],
    )
    for private_value in (
        "secret-token",
        "seller@example.com",
        "Private Listing Title",
        "private category query",
        "private provider body",
    ):
        assert private_value not in caplog.text


def test_category_requirements_return_mode_cardinality_and_selection_values(monkeypatch) -> None:
    gateway = EbayHttpGateway(environment="production")

    def fake_get(operation, path, **_parameters):
        if "get_item_aspects_for_category" in path:
            return {
                "aspects": [
                    {
                        "localizedAspectName": "Brand",
                        "aspectConstraint": {
                            "aspectRequired": True,
                            "aspectUsage": "RECOMMENDED",
                            "aspectMode": "SELECTION_ONLY",
                            "itemToAspectCardinality": "MULTI",
                            "aspectMaxLength": 80,
                            "aspectDataType": "NUMBER",
                            "aspectFormat": "double",
                            "aspectAdvancedDataType": "NUMERIC_RANGE",
                            "aspectApplicableTo": ["ITEM", "PRODUCT"],
                            "expectedRequiredByDate": "2027-01-01",
                        },
                        "aspectValues": [
                            {
                                "localizedValue": f"Brand {number}",
                                "valueConstraints": [
                                    {
                                        "applicableForLocalizedAspectName": "Department",
                                        "applicableForLocalizedAspectValues": ["Women"],
                                    }
                                ]
                                if number == 0
                                else [],
                            }
                            for number in range(876)
                        ],
                    }
                ]
            }
        return {
            "itemConditionPolicies": [
                {"itemConditions": [{"conditionId": "3000", "conditionDescription": "Used"}]}
            ]
        }

    monkeypatch.setattr(gateway, "_application_get", fake_get)

    requirements = gateway.category_requirements("3001")

    assert requirements.aspects[0].name == "Brand"
    assert requirements.aspects[0].mode == "selection"
    assert requirements.aspects[0].cardinality == "multi"
    assert requirements.aspects[0].usage == "recommended"
    assert requirements.aspects[0].recommended is False
    assert requirements.aspects[0].max_length == 80
    assert requirements.aspects[0].data_type == "number"
    assert requirements.aspects[0].format == "double"
    assert requirements.aspects[0].advanced_data_type == "numeric_range"
    assert requirements.aspects[0].applicable_to == ["item", "product"]
    assert requirements.aspects[0].expected_required_by_date == "2027-01-01"
    assert len(requirements.aspects[0].values) == 876
    assert requirements.aspects[0].values[0].value == "Brand 0"
    assert requirements.aspects[0].values[0].constraints[0].aspect_name == "Department"
    assert requirements.aspects[0].values[0].constraints[0].values == ["Women"]
    assert requirements.conditions[0].condition_id == 3000


def test_feed_upload_sends_required_file_and_file_name_fields(monkeypatch) -> None:
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/task"):
            return httpx.Response(
                202, headers={"location": "https://api.ebay.com/sell/feed/v1/task/task-123"}
            )
        return httpx.Response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    gateway = EbayHttpGateway(environment="production")

    task = gateway.create_draft_feed_task("token")
    uploaded = gateway.upload_draft_feed(
        "token", task.task_id, ebay_listing(), "LA-1-abc", ["https://i.ebayimg.com/1.jpg"]
    )

    assert task.task_id == "task-123"
    assert uploaded.status == "QUEUED"
    filename = calls[1][1]["data"]["fileName"]
    assert calls[1][1]["files"]["file"][0] == filename
    assert re.fullmatch(
        r"eBay-draft-listing-template-[A-Z][a-z]{2}-\d{2}-\d{4}(?:-\d{2}){3}\.csv",
        filename,
    )
