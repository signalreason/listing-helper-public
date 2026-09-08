from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.request import Request, urlopen

import pytest
import uvicorn
from fastapi.responses import HTMLResponse, Response
from playwright.sync_api import Page, expect

from app.ebay_models import TradingResult
from app.ebay_store import EbayConnection, MemoryEbayStore
from app.main import create_app
from tests.support import (
    GENERATION_CATEGORY,
    StubEbayGateway,
    StubGenerator,
    StubNotificationVerifier,
    authenticated_client,
    jpeg_bytes,
)


def select_generation_category(page: Page) -> None:
    if not page.locator("#generation-category").input_value():
        page.get_by_label("Category group", exact=True).select_option("260010")
        expect(page.locator("#generation-category-status")).to_have_text(
            "Select the category that matches your item."
        )
        page.get_by_label("Category for generation", exact=True).select_option("175786")


class BrowserTrading:
    def verify(self, *_args):
        return TradingResult(acknowledged=True)

    def publish(self, *_args):
        return TradingResult(acknowledged=True, item_id="123456789012")


def seed_browser_photo_usage(page: Page, byte_count: int) -> None:
    page.evaluate(
        """async ({ byteCount }) => {
          const database = await new Promise((resolve, reject) => {
            const request = indexedDB.open('listing-helper-draft-photos', 2);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          await new Promise((resolve, reject) => {
            const transaction = database.transaction('drafts', 'readwrite');
            transaction.objectStore('drafts').put({
              accountId: 999,
              draftId: 'capacity-fixture',
              bytes: byteCount,
              photoCount: 1,
            });
            transaction.oncomplete = resolve;
            transaction.onerror = () => reject(transaction.error);
          });
        }""",
        {"byteCount": byte_count},
    )


@pytest.fixture
def browser_application(monkeypatch):
    monkeypatch.setenv("APP_OPERATOR_NAME", "Example Resale")
    monkeypatch.setenv("APP_PRIVACY_EMAIL", "privacy@example.com")
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    seed_client, store, user = authenticated_client()
    ebay_store = MemoryEbayStore()
    gateway = StubEbayGateway()
    gateway.feed_status = "COMPLETED"
    ebay_store.connect(
        EbayConnection(
            user_id=user.id,
            ebay_user_id="browser-ebay-user",
            display_name="browser-seller",
            encrypted_refresh_token=seed_client.app.state.ebay_token_cipher.encrypt(
                "refresh-secret"
            ),
            status="connected",
            connected_at=datetime.now(UTC),
        )
    )
    application = create_app(
        StubGenerator(),
        store,
        gateway,
        ebay_store,
        session_secret="test-session-secret-that-is-long-enough",
        cookie_secure=False,
        ebay_token_cipher=seed_client.app.state.ebay_token_cipher,
        ebay_notification_verifier=StubNotificationVerifier(),
        trading_adapter=BrowserTrading(),
    )
    return application


@contextmanager
def running_browser_server(application):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(application, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        pytest.fail("test server did not start")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def live_server_url(browser_application):
    with running_browser_server(browser_application) as url:
        yield url


@pytest.mark.browser
def test_public_privacy_policy_is_readable_on_phone(page: Page, live_server_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/privacy")

    expect(page.get_by_role("heading", name="Privacy Policy")).to_be_visible()
    expect(page.get_by_text("We do not sell personal information", exact=False)).to_be_visible()
    expect(page.get_by_role("link", name="privacy@example.com").first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_mobile_upload_to_editable_draft(page: Page, live_server_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()

    expect(page.get_by_role("heading", name="Add photos. Get a listing draft.")).to_be_visible()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    expect(page.get_by_text("Main photo")).to_be_visible()
    expect(page.locator("#photo-count")).to_have_text("1 / 24")

    page.locator("#notes").fill("Small snag near the left cuff.")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_role("heading", name="Your listing draft")).to_be_visible()
    expect(page.get_by_label("Title")).to_have_value(
        "Everlane Green Cotton Crewneck Sweater Women's Medium"
    )
    expect(page.get_by_text("Pricing is editable LLM guidance")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_empty_saved_drafts_are_easy_to_find(page: Page, live_server_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()

    drafts_link = page.get_by_role("link", name="Drafts (0)")
    expect(drafts_link).to_be_visible()
    expect(page.get_by_role("heading", name="Saved drafts")).to_be_visible()
    expect(page.get_by_text("No saved drafts yet.", exact=False)).to_be_visible()
    drafts_link.click()
    expect(page.locator("#saved-drafts-section")).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_owner_can_create_user_without_client_error(page: Page, live_server_url: str) -> None:
    browser_errors: list[str] = []
    page.on("pageerror", lambda error: browser_errors.append(str(error)))
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Add photos. Get a listing draft.")).to_be_visible()
    page.goto(f"{live_server_url}/admin")

    page.get_by_label("New user email address").fill("second@example.com")
    page.get_by_role("button", name="Create user").click()

    expect(page.get_by_role("heading", name="Copy this password now")).to_be_visible()
    expect(page.locator("#generated-password")).not_to_be_empty()
    expect(page.get_by_label("New user email address")).to_have_value("")
    expect(page.locator("#admin-error")).to_be_hidden()
    expect(page.get_by_text("second@example.com", exact=True)).to_be_visible()
    assert browser_errors == []


@pytest.mark.browser
def test_publish_live_listing_on_phone(page: Page, live_server_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_text("Connected to browser-seller.")).to_be_visible()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_label("eBay category", exact=True)).to_have_value("175786")
    expect(page.get_by_label("eBay condition")).to_have_value("3000")
    expect(page.get_by_text("Recommended: Color.", exact=True)).to_be_visible()
    expect(page.get_by_text("Remove Brand.", exact=False)).to_have_count(0)
    expect(page.get_by_label("Add item specific")).to_contain_text("Material")
    publish = page.get_by_role("button", name="Publish live on eBay")
    expect(publish).to_be_disabled()
    expect(page.get_by_text("Enter a package weight greater than zero.")).to_be_visible()
    expect(page.get_by_text("Enter a package length greater than zero.")).to_be_visible()
    expect(page.get_by_text("Enter a package width greater than zero.")).to_be_visible()
    expect(page.get_by_text("Enter a package height greater than zero.")).to_be_visible()
    page.get_by_label("Ounces").fill("16")
    expect(page.get_by_text("Enter 0 to 15 whole ounces.")).to_be_visible()
    expect(page.get_by_label("Ounces")).to_have_attribute("aria-invalid", "true")
    page.get_by_label("Ounces").fill("14")
    expect(publish).to_be_disabled()
    page.get_by_label("Length").fill("15")
    width = page.get_by_label("Width")
    width.fill("11.5")
    width.press("Tab")
    page.get_by_label("Height").fill("1")
    expect(width).to_have_value("12")
    expect(page.get_by_text("Width was rounded up from 11.5 to 12 inches.")).to_be_visible()
    expect(page.get_by_text("Recommended: Color.", exact=True)).to_be_visible()
    expect(publish).to_be_enabled()

    brand = page.get_by_label("Item specific 1 value")
    brand.fill("")
    expect(
        page.get_by_text("Complete the required eBay item specifics shown above.")
    ).to_be_visible()
    expect(brand).to_have_attribute("aria-invalid", "true")
    expect(page.locator(".specific-row").first.locator("[data-specific-error]")).to_contain_text(
        "Enter a value for Brand."
    )
    expect(publish).to_be_disabled()
    brand.fill("Everlane")
    expect(publish).to_be_enabled()
    publish.click()

    expect(page.get_by_text("Listing published.", exact=False)).to_be_visible()
    expect(page.get_by_role("link", name="View listing")).to_have_attribute(
        "href", "https://www.ebay.com/itm/123456789012"
    )
    expect(page.get_by_role("link", name="Open Seller Hub active listings")).to_have_attribute(
        "href", "https://www.ebay.com/sh/lst/active"
    )
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    expect(page.locator("#saved-drafts-list")).to_contain_text("Published")
    expect(page.get_by_label("Title")).to_be_disabled()
    expect(page.get_by_text("published and read-only", exact=False)).to_be_visible()
    expect(page.locator("#photo-input")).to_be_disabled()
    expect(page.locator("#notes")).to_be_disabled()
    expect(page.get_by_role("button", name="Remove photo 1")).to_be_disabled()
    expect(page.get_by_role("button", name="Generate listing")).to_be_disabled()

    page.get_by_role("button", name="Start new listing").click()
    expect(page.locator("#result-section")).to_be_hidden()
    expect(page.locator("#photo-input")).to_be_enabled()
    expect(page.locator("#notes")).to_be_enabled()
    expect(page.locator("#photo-count")).to_have_text("0 / 24")
    expect(page.locator("#saved-drafts-list")).to_contain_text("Published")
    page.locator("#photo-input").set_input_files(
        {"name": "next-item.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.get_by_label("Ounces")).to_be_enabled()
    expect(page.get_by_label("Length")).to_be_enabled()
    expect(page.get_by_label("Shipping policy")).to_be_enabled()

    published_row = page.locator(".saved-draft-row").filter(has_text="Published")
    published_row.get_by_role("button", name="Open").click()
    expect(page.get_by_text("published and read-only", exact=False)).to_be_visible()
    expect(page.locator("#notes")).to_be_disabled()
    page.on("dialog", lambda dialog: dialog.accept())
    published_row.get_by_role("button", name="Delete").click()
    expect(page.locator("#result-section")).to_be_hidden()
    expect(page.locator("#photo-input")).to_be_enabled()
    expect(page.locator("#notes")).to_be_enabled()
    expect(page.get_by_role("button", name="Generate listing")).to_be_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_publication_blocks_opening_another_draft(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("button", name="Generate listing")).to_be_enabled()
    page.get_by_label("Title").fill("Draft B")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    page.get_by_label("Title").fill("Draft A")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")

    page.get_by_label("Ounces").fill("8")
    page.get_by_label("Length").fill("12")
    page.get_by_label("Width").fill("8")
    page.get_by_label("Height").fill("3")
    publish = page.get_by_role("button", name="Publish live on eBay")
    expect(publish).to_be_enabled()
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            const url = String(resource);
            if (!url.includes('/api/ebay/listings/') || !url.endsWith('/publish')) {
              return originalFetch(resource, options);
            }
            return new Promise((resolve, reject) => {
              window.releasePublication = () => {
                originalFetch(resource, options).then(resolve, reject);
              };
            });
          };
        }"""
    )
    publish.click()
    page.wait_for_function("typeof window.releasePublication === 'function'")

    open_buttons = page.locator("#saved-drafts-list").get_by_role("button", name="Open")
    expect(open_buttons).to_have_count(2)
    expect(open_buttons.nth(0)).to_be_disabled()
    expect(open_buttons.nth(1)).to_be_disabled()
    expect(page.get_by_label("Title")).to_be_disabled()
    expect(page.locator("#photo-input")).to_be_disabled()
    expect(page.get_by_label("Ounces")).to_be_disabled()
    open_buttons.nth(1).evaluate("button => button.click()")
    expect(page.get_by_label("Title")).to_have_value("Draft A")

    page.evaluate("window.releasePublication()")
    expect(page.get_by_text("Listing published.", exact=False)).to_be_visible()
    expect(page.get_by_label("Title")).to_have_value("Draft A")
    expect(page.get_by_label("Title")).to_be_disabled()

    expect(page.locator("#saved-drafts-list")).to_contain_text("Draft A")
    expect(page.locator("#saved-drafts-list")).to_contain_text("Draft B")
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            if (!String(resource).includes('/api/ebay/categories?q=')) {
              return originalFetch(resource, options);
            }
            return new Promise((resolve, reject) => {
              window.releaseCategorySearch = () => {
                window.fetch = originalFetch;
                originalFetch(resource, options).then(resolve, reject);
              };
            });
          };
        }"""
    )
    draft_b = page.locator(".saved-draft-row").filter(has_text="Draft B")
    draft_a = page.locator(".saved-draft-row").filter(has_text="Draft A")
    draft_b.get_by_role("button", name="Open").click()
    page.get_by_role("button", name="Find other categories for this draft").click()
    page.wait_for_function("typeof window.releaseCategorySearch === 'function'")
    expect(page.get_by_label("Title")).to_have_value("Draft B")
    draft_a.get_by_role("button", name="Open").click()
    expect(page.get_by_label("Title")).to_have_value("Draft A")
    expect(page.get_by_label("Title")).to_be_disabled()

    page.evaluate("window.releaseCategorySearch()")
    expect(page.get_by_label("eBay category", exact=True)).to_be_disabled()
    expect(page.get_by_label("eBay condition")).to_be_disabled()
    expect(page.get_by_role("button", name="Published")).to_be_disabled()

    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            const url = String(resource);
            if (!url.includes('/api/ebay/categories/') || !url.endsWith('/requirements')) {
              return originalFetch(resource, options);
            }
            return new Promise((resolve, reject) => {
              window.releaseCategoryRequirements = () => {
                window.fetch = originalFetch;
                originalFetch(resource, options).then(resolve, reject);
              };
            });
          };
        }"""
    )
    draft_b.get_by_role("button", name="Open").click()
    page.wait_for_function("typeof window.releaseCategoryRequirements === 'function'")
    expect(page.get_by_label("Title")).to_have_value("Draft B")
    draft_a.get_by_role("button", name="Open").click()
    expect(page.get_by_label("Title")).to_have_value("Draft A")
    page.evaluate("window.releaseCategoryRequirements()")

    expect(page.get_by_label("eBay category", exact=True)).to_be_disabled()
    expect(page.get_by_label("eBay condition")).to_be_disabled()
    expect(page.get_by_role("button", name="Published")).to_be_disabled()


@pytest.mark.browser
def test_category_rules_control_multi_values_constraints_and_saved_shape(
    page: Page, live_server_url: str
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_label("Item specific 1 name")).to_have_attribute("readonly", "")
    picker = page.get_by_label("Add item specific")
    expect(picker).not_to_contain_text("Seller field")
    picker.select_option("Material")
    page.get_by_label("Item specific 3 value", exact=True).fill("Cotton")
    material_row = page.locator(".specific-row").nth(2)
    material_row.get_by_role("button", name="Add another value").click()
    page.get_by_label("Item specific 3 value 2", exact=True).fill("Wool")

    page.get_by_label("Add item specific").select_option("Metal Purity")
    purity = page.get_by_label("Item specific 4 value", exact=True)
    purity.fill("10k")
    expect(
        page.get_by_text(
            "10k is not available with the selected related item specifics.", exact=True
        )
    ).to_be_visible()

    page.get_by_label("Add item specific").select_option("Metal")
    page.get_by_label("Item specific 5 value", exact=True).fill("Gold")
    expect(
        page.get_by_text(
            "10k is not available with the selected related item specifics.", exact=True
        )
    ).to_be_hidden()

    page.get_by_label("Add item specific").select_option("Short note")
    expect(page.get_by_label("Item specific 6 value", exact=True)).to_have_attribute(
        "maxlength", "5"
    )
    expect(page.locator(".specific-row").nth(5).locator("[data-specific-length]")).to_have_text(
        "0/5"
    )
    page.get_by_label("Title").fill("Saved multi-value sweater")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.reload()
    page.get_by_role("link", name="Drafts (1)").click()
    saved_row = page.locator(".saved-draft-row").filter(has_text="Saved multi-value sweater")
    saved_row.get_by_role("button", name="Open").click()

    expect(page.get_by_label("Item specific 3 value", exact=True)).to_have_value("Cotton")
    expect(page.get_by_label("Item specific 3 value 2", exact=True)).to_have_value("Wool")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_category_rules_remove_unlisted_specific_and_add_required_measurement(
    page: Page, live_server_url: str
) -> None:
    generated = StubGenerator().draft.model_dump(mode="json")
    generated["item_specifics"].append(
        {
            "name": "Pockets",
            "values": ["5-pocket design"],
            "source": "photo",
            "confidence": "high",
        }
    )

    def add_required_inseam(route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["aspects"].append(
            {
                "name": "Inseam",
                "values": [],
                "required": True,
                "recommended": False,
                "usage": "optional",
                "mode": "free_text",
                "cardinality": "single",
                "max_length": 65,
                "data_type": "string",
                "format": None,
                "advanced_data_type": None,
                "applicable_to": ["item"],
                "expected_required_by_date": None,
            }
        )
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route(
        "**/api/listings/generate",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "draft_id": str(uuid.uuid4()),
                    "ebay_category": GENERATION_CATEGORY,
                    "saved": False,
                    "draft": generated,
                    "warnings": [],
                }
            ),
        ),
    )
    page.route("**/api/ebay/categories/*/requirements", add_required_inseam)
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_label("Item specific 3 name")).to_have_value("Inseam")
    expect(page.locator(".specific-row")).to_have_count(3)
    specific_names = page.locator("[data-specific-name]").evaluate_all(
        "(fields) => fields.map((field) => field.value)"
    )
    assert "Pockets" not in specific_names
    expect(page.get_by_text("Remove Pockets.", exact=False)).to_have_count(0)
    inseam_name = page.get_by_label("Item specific 3 name")
    expect(inseam_name).to_have_value("Inseam")
    inseam_row = inseam_name.locator("..")
    expect(inseam_row.get_by_text("Required by eBay", exact=True)).to_be_visible()
    expect(inseam_row.get_by_role("button", name="Remove item specific 3")).to_be_hidden()
    expect(inseam_row.locator("[data-specific-error]")).to_have_text("Enter a value for Inseam.")

    page.get_by_label("Item specific 3 value", exact=True).fill("30 in")

    expect(inseam_row.locator("[data-specific-error]")).to_be_hidden()
    expect(inseam_row.get_by_text("seller note · high confidence", exact=True)).to_be_visible()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    saved_specifics = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json())"
        ".then((data) => fetch(`/api/drafts/${data.drafts[0].id}`))"
        ".then((response) => response.json())"
        ".then((data) => data.draft.item_specifics)"
    )
    assert [specific["name"] for specific in saved_specifics] == ["Brand", "Size", "Inseam"]
    assert saved_specifics[-1]["source"] == "seller_note"
    assert saved_specifics[-1]["confidence"] == "high"


@pytest.mark.browser
def test_duplicate_required_specific_can_be_removed(page: Page, live_server_url: str) -> None:
    generated = StubGenerator().draft.model_copy(deep=True)
    generated.item_specifics.append(generated.item_specifics[0].model_copy(deep=True))
    generated.item_specifics[-1].values = ["Duplicate brand"]
    page.route(
        "**/api/listings/generate",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "draft_id": str(uuid.uuid4()),
                    "ebay_category": GENERATION_CATEGORY,
                    "saved": False,
                    "draft": generated.model_dump(mode="json"),
                    "warnings": [],
                }
            ),
        ),
    )
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_text("Use Brand only once.", exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Remove item specific 1")).to_be_visible()
    duplicate_remove = page.get_by_role("button", name="Remove item specific 3")
    expect(duplicate_remove).to_be_visible()
    duplicate_remove.click()

    expect(page.locator(".specific-row")).to_have_count(2)
    expect(page.get_by_role("button", name="Remove item specific 1")).to_be_hidden()
    expect(page.get_by_text("Use Brand only once.", exact=True)).to_be_hidden()


@pytest.mark.browser
def test_unsupported_taxonomy_rule_blocks_publication(page: Page, live_server_url: str) -> None:
    def return_future_rule(route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["aspects"][0]["mode"] = "future_mode"
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/ebay/categories/*/requirements", return_future_rule)
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(
        page.get_by_text(
            "eBay returned an unsupported mode future_mode rule for Brand.", exact=True
        ).first
    ).to_be_visible()
    expect(page.get_by_role("button", name="Publish live on eBay")).to_be_disabled()


@pytest.mark.browser
def test_required_apparel_product_aspects_remain_editable(page: Page, live_server_url: str) -> None:
    def return_product_rules(route) -> None:
        response = route.fetch()
        payload = response.json()
        for aspect in payload["aspects"]:
            if aspect["name"] in {"Brand", "Size"}:
                aspect["applicable_to"] = ["product"]
        payload["aspects"].extend(
            [
                {
                    "name": "Type",
                    "values": [{"value": "Jeans", "constraints": []}],
                    "required": True,
                    "recommended": False,
                    "usage": "optional",
                    "mode": "selection",
                    "cardinality": "single",
                    "max_length": 65,
                    "data_type": "string",
                    "format": None,
                    "advanced_data_type": None,
                    "applicable_to": ["product"],
                    "expected_required_by_date": None,
                },
                {
                    "name": "Department",
                    "values": [{"value": "Men", "constraints": []}],
                    "required": True,
                    "recommended": False,
                    "usage": "optional",
                    "mode": "selection",
                    "cardinality": "single",
                    "max_length": 65,
                    "data_type": "string",
                    "format": None,
                    "advanced_data_type": None,
                    "applicable_to": ["product"],
                    "expected_required_by_date": None,
                },
            ]
        )
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/ebay/categories/*/requirements", return_product_rules)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "jeans.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_label("Item specific 1 value", exact=True)).to_have_value("Everlane")
    expect(page.get_by_label("Item specific 2 value", exact=True)).to_have_value("M")
    page.get_by_label("Item specific 3 value", exact=True).fill("Jeans")
    page.get_by_label("Item specific 4 value", exact=True).fill("Men")

    expect(page.locator("#ebay-requirements")).not_to_contain_text("Remove Brand")
    expect(page.locator("#ebay-requirements")).not_to_contain_text("Remove Type")
    expect(page.locator("#ebay-requirements")).not_to_contain_text("Remove Department")
    expect(page.locator("#ebay-requirements")).not_to_contain_text("Remove Size")
    expect(page.get_by_label("Item specific 3 value", exact=True)).to_have_attribute(
        "aria-invalid", "false"
    )
    expect(page.get_by_label("Item specific 4 value", exact=True)).to_have_attribute(
        "aria-invalid", "false"
    )


@pytest.mark.browser
def test_category_change_preserves_values_not_listed_for_new_category(
    page: Page, live_server_url: str
) -> None:
    def return_two_categories(route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["categories"].append(
            {
                "category_id": "999",
                "name": "Coats",
                "path": "Clothing > Coats",
            }
        )
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    def return_coat_rules(route) -> None:
        response = route.fetch()
        payload = response.json()
        payload["category"] = {
            "category_id": "999",
            "name": "Coats",
            "path": "Clothing > Coats",
        }
        payload["aspects"] = [
            {
                "name": "Size",
                "values": [
                    {"value": "S", "constraints": []},
                    {"value": "M", "constraints": []},
                    {"value": "L", "constraints": []},
                ],
                "required": True,
                "recommended": False,
                "usage": "optional",
                "mode": "selection",
                "cardinality": "single",
                "max_length": 65,
                "data_type": "string",
                "format": None,
                "advanced_data_type": None,
                "applicable_to": [],
                "expected_required_by_date": None,
            },
            {
                "name": "Model",
                "values": [],
                "required": True,
                "recommended": False,
                "usage": "optional",
                "mode": "free_text",
                "cardinality": "single",
                "max_length": 12,
                "data_type": "string",
                "format": None,
                "advanced_data_type": None,
                "applicable_to": [],
                "expected_required_by_date": None,
            },
        ]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/ebay/categories?*", return_two_categories)
    page.route("**/api/ebay/categories/999/requirements", return_coat_rules)
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    page.get_by_role("button", name="Find other categories for this draft").click()
    page.get_by_label("eBay category", exact=True).select_option("999")

    expect(page.locator(".specific-row")).to_have_count(3)
    expect(page.get_by_label("Item specific 2 name")).to_have_value("Size")
    expect(page.get_by_label("Item specific 2 name")).to_have_attribute("readonly", "")
    expect(page.get_by_label("Item specific 2 value", exact=True)).to_have_value("M")
    expect(page.get_by_label("Item specific 3 name")).to_have_value("Model")
    expect(page.get_by_label("Item specific 3 name")).to_have_attribute("readonly", "")
    expect(page.get_by_label("Item specific 3 value", exact=True)).to_have_attribute(
        "maxlength", "12"
    )
    specific_values = page.locator("[data-specific-value]").evaluate_all(
        "(fields) => fields.map((field) => field.value)"
    )
    assert "Everlane" in specific_values
    expect(page.locator(".specific-row").nth(0)).to_contain_text("Remove Brand.")
    expect(page.get_by_text("Required by eBay", exact=True)).to_be_visible()
    expect(page.get_by_label("Item specific 3 value", exact=True)).to_have_count(1)
    expect(page.get_by_role("button", name="Remove item specific 2")).to_be_hidden()
    expect(page.get_by_role("button", name="Publish live on eBay")).to_be_disabled()

    page.get_by_label("eBay category", exact=True).select_option("")

    expect(page.get_by_label("Item specific 3 name")).not_to_have_attribute("readonly", "")
    assert page.get_by_label("Item specific 3 value", exact=True).get_attribute("maxlength") is None
    expect(page.get_by_role("button", name="Remove item specific 2")).to_be_visible()
    expect(page.get_by_role("button", name="Add custom item specific")).to_be_visible()
    expect(page.locator("#ebay-requirements")).to_be_hidden()
    page.get_by_label("Item specific 3 value", exact=True).fill("Seller model")
    expect(page.locator(".specific-row").nth(2)).to_have_attribute("data-source", "seller_note")
    expect(page.get_by_text("seller note · high confidence", exact=True)).to_be_visible()


@pytest.mark.browser
def test_missing_business_policy_explains_disabled_publish(
    page: Page, live_server_url: str
) -> None:
    page.route(
        "**/api/ebay/business-policies",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"policies":['
                '{"type":"payment_policy","id":"payment","name":"Managed payments"},'
                '{"type":"return_policy","id":"returns","name":"30 day returns"}'
                "]}"
            ),
        ),
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_text("Create a fulfillment policy", exact=False)).to_be_visible()
    expect(page.get_by_text("Create the missing eBay business policy shown above.")).to_be_visible()
    expect(page.get_by_role("button", name="Publish live on eBay")).to_be_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_disabled_live_publication_has_a_visible_explanation(
    page: Page, live_server_url: str
) -> None:
    page.route(
        "**/api/ebay/connection",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"configured":true,"connected":true,"status":"connected",'
                '"display_name":"browser-seller","direct_publish_enabled":false}'
            ),
        ),
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_role("heading", name="Publish this reviewed listing")).to_be_visible()
    expect(
        page.get_by_text("Live eBay publication is not enabled on this server.", exact=False)
    ).to_be_visible()
    publish = page.get_by_role("button", name="Publish live on eBay")
    expect(publish).to_be_disabled()
    expect(page.get_by_label("Item specific 1 name")).not_to_have_attribute("readonly", "")
    expect(page.get_by_role("button", name="Add custom item specific")).to_be_visible()
    expect(
        page.locator(".specific-row").first.get_by_role("button", name="Add another value")
    ).to_be_visible()
    assert publish.evaluate("element => getComputedStyle(element).cursor") == "not-allowed"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_failed_initial_save_can_retry_without_losing_generated_draft(
    page: Page, live_server_url: str
) -> None:
    draft_id = str(uuid.uuid4())
    generation_response = {
        "draft_id": draft_id,
        "saved": False,
        "draft": StubGenerator().draft.model_dump(mode="json"),
        "warnings": [],
    }
    page.route(
        "**/api/listings/generate",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(generation_response),
        ),
    )
    page.route(
        "**/api/ebay/connection",
        lambda route: route.fulfill(
            status=503,
            content_type="application/problem+json",
            body='{"code":"ebay_unavailable","detail":"Unavailable"}',
        ),
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.locator("#draft-save-status")).to_have_text("Not saved")
    expect(
        page.get_by_text("Keep this page open and select Save now.", exact=False)
    ).to_be_visible()
    save_now = page.get_by_role("button", name="Save now")
    expect(save_now).to_be_visible()
    expect(page.get_by_role("button", name="Publish live on eBay")).to_be_visible()
    save_now.click()

    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.locator("#draft-save-message")).to_be_hidden()
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    page.reload()
    saved_row = page.locator(".saved-draft-row")
    saved_row.get_by_role("button", name="Open").click()
    expect(page.get_by_alt_text("Selected photo 1: sweater.jpg")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_connection_failure_keeps_publish_action_visible(page: Page, live_server_url: str) -> None:
    page.route(
        "**/api/ebay/connection",
        lambda route: route.fulfill(
            status=503,
            content_type="application/problem+json",
            body='{"code":"ebay_unavailable","detail":"Unavailable"}',
        ),
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_role("heading", name="Publish this reviewed listing")).to_be_visible()
    expect(page.locator("#ebay-transfer-status")).to_contain_text(
        "The eBay connection status is not available."
    )
    expect(page.get_by_role("button", name="Publish live on eBay")).to_be_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_disconnect_keeps_publish_action_visible(page: Page, live_server_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("button", name="Publish live on eBay")).to_be_visible()
    expect(page.get_by_label("Item specific 1 name")).to_have_attribute("readonly", "")

    page.get_by_role("button", name="Disconnect").click()

    expect(page.get_by_role("heading", name="Publish this reviewed listing")).to_be_visible()
    expect(page.locator("#ebay-transfer-status")).to_contain_text(
        "Connect your eBay seller account before you publish."
    )
    expect(page.get_by_role("button", name="Publish live on eBay")).to_be_disabled()
    expect(page.get_by_label("Item specific 1 name")).not_to_have_attribute("readonly", "")
    assert page.get_by_label("Item specific 1 value").get_attribute("maxlength") is None
    expect(page.get_by_role("button", name="Add custom item specific")).to_be_visible()
    page.get_by_label("Item specific 1 value").fill("Changed maker")
    expect(page.get_by_label("Item specific 1 name")).not_to_have_attribute("readonly", "")
    assert page.get_by_label("Item specific 1 value").get_attribute("maxlength") is None
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
@pytest.mark.parametrize(
    ("failure_code", "policies_frozen"),
    [
        ("ebay_publish_uncertain", True),
        ("ebay_photos_expired_recovery", True),
        ("ebay_verification_unavailable_recovery", True),
        ("ebay_verification_unavailable", False),
        ("ebay_unavailable", True),
        ("transport_error", True),
    ],
)
def test_mobile_publish_failure_can_resume_when_draft_save_is_locked(
    page: Page, live_server_url: str, failure_code: str, policies_frozen: bool
) -> None:
    publish_attempts = 0
    locked_save_attempts = 0

    def fail_first_publish(route) -> None:
        nonlocal publish_attempts
        publish_attempts += 1
        if publish_attempts == 1:
            if failure_code == "transport_error":
                route.abort("failed")
                return
            route.fulfill(
                status=502,
                content_type="application/problem+json",
                body=json.dumps(
                    {
                        "code": failure_code,
                        "detail": "eBay did not confirm publication. Retry safely.",
                    }
                ),
            )
        else:
            route.continue_()

    page.set_viewport_size({"width": 390, "height": 844})
    page.route("**/api/ebay/listings/*/publish", fail_first_publish)
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.get_by_label("Ounces").fill("8")
    page.get_by_label("Length").fill("12")
    page.get_by_label("Width").fill("8")
    page.get_by_label("Height").fill("3")
    page.get_by_role("button", name="Publish live on eBay").click()

    retry = page.get_by_role("button", name="Retry publish")
    expect(retry).to_be_enabled()
    if failure_code != "transport_error":
        expect(page.get_by_text("Retry safely.", exact=False)).to_be_visible()
    for name in ["Shipping policy", "Payment policy", "Return policy"]:
        policy = page.get_by_label(name)
        if policies_frozen:
            expect(policy).to_be_disabled()
        else:
            expect(policy).to_be_enabled()

    def reject_locked_draft_save(route) -> None:
        nonlocal locked_save_attempts
        if route.request.method != "PUT":
            route.continue_()
            return
        locked_save_attempts += 1
        route.fulfill(
            status=409,
            content_type="application/problem+json",
            body=(
                '{"code":"draft_publication_in_progress","detail":"Publication is in progress."}'
            ),
        )

    page.route("**/api/drafts/*", reject_locked_draft_save)
    retry.click()
    expect(page.get_by_text("Listing published.", exact=False)).to_be_visible()
    assert publish_attempts == 2
    assert locked_save_attempts == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_transient_preparation_retry_keeps_publication_recovery_identity(
    page: Page, live_server_url: str
) -> None:
    publish_attempts = 0
    preparation_attempts: list[bytes] = []

    def fail_first_publish(route) -> None:
        nonlocal publish_attempts
        publish_attempts += 1
        if publish_attempts == 1:
            route.fulfill(
                status=502,
                content_type="application/problem+json",
                body=(
                    '{"code":"ebay_publish_uncertain",'
                    '"detail":"eBay did not confirm publication. Retry safely."}'
                ),
            )
        else:
            route.continue_()

    def fail_second_preparation(route) -> None:
        preparation_attempts.append(route.request.post_data_buffer or b"")
        if len(preparation_attempts) == 2:
            route.fulfill(
                status=502,
                content_type="application/problem+json",
                body='{"code":"ebay_request_failed","detail":"eBay policies are unavailable."}',
            )
        else:
            route.continue_()

    page.route("**/api/ebay/listings/*/publish", fail_first_publish)
    page.route("**/api/ebay/live-listing-preparations", fail_second_preparation)
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.get_by_label("Ounces").fill("8")
    page.get_by_label("Length").fill("12")
    page.get_by_label("Width").fill("8")
    page.get_by_label("Height").fill("3")
    page.get_by_role("button", name="Publish live on eBay").click()

    retry = page.get_by_role("button", name="Retry publish")
    expect(retry).to_be_enabled()
    retry.click()
    expect(page.get_by_text("eBay policies are unavailable.", exact=False)).to_be_visible()
    expect(retry).to_be_enabled()
    expect(page.get_by_label("Shipping policy")).to_be_disabled()
    retry.click()

    expect(page.get_by_text("Listing published.", exact=False)).to_be_visible()
    assert publish_attempts == 2
    assert len(preparation_attempts) == 3
    recovery_field = b'name="recovery_preparation_id"'
    assert recovery_field in preparation_attempts[1]
    assert recovery_field in preparation_attempts[2]


@pytest.mark.browser
def test_reopened_publication_recovery_skips_loader_save(page: Page, live_server_url: str) -> None:
    publish_attempts = 0
    locked_save_attempts = 0
    recovery_loader_attempts = 0

    def current_policies(route) -> None:
        nonlocal recovery_loader_attempts
        if publish_attempts:
            recovery_loader_attempts += 1
            route.fulfill(
                status=503,
                content_type="application/problem+json",
                body='{"detail":"Current eBay policies are unavailable."}',
            )
            return
        shipping_policy = (
            '{"type":"fulfillment_policy","id":"shipping","name":"Standard shipping"},'
            if publish_attempts == 0
            else ""
        )
        route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"policies":['
                f"{shipping_policy}"
                '{"type":"fulfillment_policy","id":"pickup","name":"Local pickup"},'
                '{"type":"payment_policy","id":"payment","name":"Managed payments"},'
                '{"type":"payment_policy","id":"payment-2","name":"Other payments"},'
                '{"type":"return_policy","id":"returns","name":"30 day returns"},'
                '{"type":"return_policy","id":"returns-2","name":"No returns"}'
                "]}"
            ),
        )

    page.route("**/api/ebay/business-policies", current_policies)

    def fail_recovery_category_loaders(route) -> None:
        nonlocal recovery_loader_attempts
        if publish_attempts:
            recovery_loader_attempts += 1
            route.fulfill(
                status=503,
                content_type="application/problem+json",
                body='{"detail":"Current eBay category data is unavailable."}',
            )
            return
        route.continue_()

    page.route("**/api/ebay/categories**", fail_recovery_category_loaders)

    def fail_first_publish(route) -> None:
        nonlocal publish_attempts
        publish_attempts += 1
        if publish_attempts == 1:
            route.fulfill(
                status=502,
                content_type="application/problem+json",
                body=(
                    '{"code":"ebay_publish_uncertain",'
                    '"detail":"eBay did not confirm publication. Retry safely."}'
                ),
            )
        else:
            route.continue_()

    page.route("**/api/ebay/listings/*/publish", fail_first_publish)
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.get_by_label("Ounces").fill("8")
    page.get_by_label("Length").fill("12")
    page.get_by_label("Width").fill("8")
    page.get_by_label("Height").fill("3")
    page.get_by_label("Shipping policy").select_option("shipping")
    page.get_by_label("Payment policy").select_option("payment")
    page.get_by_label("Return policy").select_option("returns")
    page.get_by_role("button", name="Publish live on eBay").click()
    expect(page.get_by_role("button", name="Retry publish")).to_be_enabled()

    def expose_recovery(route) -> None:
        nonlocal locked_save_attempts
        if route.request.method == "PUT":
            locked_save_attempts += 1
            route.fulfill(
                status=409,
                content_type="application/problem+json",
                body=(
                    '{"code":"draft_publication_in_progress",'
                    '"detail":"Publication is in progress."}'
                ),
            )
            return
        if route.request.method != "GET":
            route.continue_()
            return
        response = route.fetch()
        payload = response.json()
        payload["publication_recovery_pending"] = True
        payload["publication_recovery_policies"] = {
            "fulfillment_policy_id": "shipping",
            "payment_policy_id": "payment",
            "return_policy_id": "returns",
        }
        payload["publication_recovery_preparation_id"] = "protected-preparation"
        route.fulfill(response=response, json=payload)

    page.route("**/api/drafts/*", expose_recovery)
    recovery_preparations: list[bytes] = []
    page.on(
        "request",
        lambda request: (
            recovery_preparations.append(request.post_data_buffer or b"")
            if request.url.endswith("/api/ebay/live-listing-preparations")
            else None
        ),
    )
    page.add_init_script(
        """(() => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            if (String(resource) !== '/api/ebay/connection') {
              return originalFetch(resource, options);
            }
            return new Promise((resolve, reject) => {
              window.releaseEbayConnection = () => {
                originalFetch(resource, options).then(resolve, reject);
              };
            });
          };
        })()"""
    )
    page.reload()
    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    expect(page.locator("#ebay-transfer-status")).to_contain_text("Checking your eBay connection")
    page.evaluate("window.releaseEbayConnection()")

    retry = page.get_by_role("button", name="Retry publish")
    expect(retry).to_be_enabled()
    expect(page.get_by_label("Shipping policy")).to_be_disabled()
    expect(page.get_by_label("Shipping policy")).to_have_value("shipping")
    expect(page.get_by_label("Shipping policy").locator("option:checked")).to_have_text(
        "Saved policy from protected publication"
    )
    expect(page.get_by_label("Payment policy")).to_be_disabled()
    expect(page.get_by_label("Payment policy")).to_have_value("payment")
    expect(page.get_by_label("Return policy")).to_be_disabled()
    expect(page.get_by_label("Return policy")).to_have_value("returns")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    retry.click()

    expect(page.get_by_text("Listing published.", exact=False)).to_be_visible()
    assert publish_attempts == 2
    assert locked_save_attempts == 0
    assert recovery_loader_attempts == 0
    assert len(recovery_preparations) == 1
    assert b"protected-preparation" in recovery_preparations[0]


@pytest.mark.browser
def test_publication_recovery_does_not_skip_unsaved_changes(
    page: Page, live_server_url: str
) -> None:
    publish_attempts = 0

    def fail_publication(route) -> None:
        nonlocal publish_attempts
        publish_attempts += 1
        route.fulfill(
            status=502,
            content_type="application/problem+json",
            body=(
                '{"code":"ebay_publish_uncertain",'
                '"detail":"eBay did not confirm publication. Retry safely."}'
            ),
        )

    page.route("**/api/ebay/listings/*/publish", fail_publication)
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.get_by_label("Ounces").fill("8")
    page.get_by_label("Length").fill("12")
    page.get_by_label("Width").fill("8")
    page.get_by_label("Height").fill("3")
    page.get_by_role("button", name="Publish live on eBay").click()
    retry = page.get_by_role("button", name="Retry publish")
    expect(retry).to_be_enabled()

    def reject_locked_draft_save(route) -> None:
        if route.request.method != "PUT":
            route.continue_()
            return
        route.fulfill(
            status=409,
            content_type="application/problem+json",
            body=(
                '{"code":"draft_publication_in_progress","detail":"Publication is in progress."}'
            ),
        )

    page.route("**/api/drafts/*", reject_locked_draft_save)
    page.evaluate(
        """() => {
          for (const id of [
            'fulfillment-policy',
            'payment-policy',
            'return-policy',
          ]) document.getElementById(id).disabled = false;
        }"""
    )
    page.get_by_label("Title").fill("Unsaved recovery edit")
    retry.click()

    expect(page.locator("#ebay-transfer-status")).to_have_text(
        "Save this draft before you publish it."
    )
    expect(page.get_by_text("Listing published.", exact=False)).to_have_count(0)
    expect(page.get_by_label("Shipping policy")).to_be_disabled()
    expect(page.get_by_label("Payment policy")).to_be_disabled()
    expect(page.get_by_label("Return policy")).to_be_disabled()
    assert publish_attempts == 1


@pytest.mark.browser
def test_stale_photo_revision_blocks_publication(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.get_by_label("Ounces").fill("8")
    page.get_by_label("Length").fill("12")
    page.get_by_label("Width").fill("8")
    page.get_by_label("Height").fill("3")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """async () => {
          const store = await import('/static/photo-store.js');
          const drafts = await fetch('/api/drafts').then((response) => response.json());
          const session = await fetch('/api/session').then((response) => response.json());
          const draftId = drafts.drafts[0].id;
          const serverDraft = await fetch(`/api/drafts/${draftId}`)
            .then((response) => response.json());
          const database = await store.openPhotoStore();
          await store.advanceDraftPhotoRevision(
            database,
            session.user.id,
            draftId,
            serverDraft.revision,
            serverDraft.revision + 10,
          );
        }"""
    )
    preparation_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            preparation_requests.append(request.url)
            if request.url.endswith("/api/ebay/live-listing-preparations")
            else None
        ),
    )

    page.get_by_label("Title").fill("Stale tab title")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.get_by_text("A newer version of this draft", exact=False)).to_be_visible()
    page.get_by_role("button", name="Publish live on eBay").click()

    expect(page.locator("#ebay-transfer-status")).to_contain_text(
        "Reload the draft before you continue."
    )
    assert preparation_requests == []


@pytest.mark.browser
def test_clean_stale_tab_cannot_prepare_a_newer_server_draft(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.get_by_label("Ounces").fill("8")
    page.get_by_label("Length").fill("12")
    page.get_by_label("Width").fill("8")
    page.get_by_label("Height").fill("3")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    draft_id = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json()).then((body) => body.drafts[0].id)"
    )
    cookie = "; ".join(
        f"{item['name']}={item['value']}" for item in page.context.cookies(live_server_url)
    )

    def advance_draft_before_preparation(route) -> None:
        with urlopen(
            Request(f"{live_server_url}/api/drafts/{draft_id}", headers={"Cookie": cookie})
        ) as response:
            saved = json.load(response)
        saved["draft"]["title"] = "Newer tab title"
        body = json.dumps(
            {
                "revision": saved["revision"],
                "draft": saved["draft"],
                "ebay_category": saved["ebay_category"],
                "ebay_condition": saved["ebay_condition"],
                "photo_count": saved["photo_count"],
            }
        ).encode()
        with urlopen(
            Request(
                f"{live_server_url}/api/drafts/{draft_id}",
                data=body,
                method="PUT",
                headers={
                    "Content-Type": "application/json",
                    "Cookie": cookie,
                    "Origin": live_server_url,
                },
            )
        ) as response:
            assert response.status == 200
        route.continue_()

    page.route("**/api/ebay/live-listing-preparations", advance_draft_before_preparation)
    publish_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            publish_requests.append(request.url)
            if "/api/ebay/listings/" in request.url and request.url.endswith("/publish")
            else None
        ),
    )

    page.get_by_role("button", name="Publish live on eBay").click()

    expect(page.locator("#ebay-transfer-status")).to_contain_text(
        "Reload the draft before you publish it."
    )
    assert publish_requests == []


@pytest.mark.browser
def test_generated_draft_can_be_reopened_after_reload(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_role("heading", name="Saved drafts")).to_be_visible()
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    page.get_by_label("Title").fill("Edited saved sweater title")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.locator("#saved-drafts-list")).to_contain_text("Edited saved sweater title")
    page.reload()
    page.get_by_role("link", name="Drafts (1)").click()
    expect(page.locator("#saved-drafts-section")).to_be_focused()
    saved_row = page.locator(".saved-draft-row").filter(has_text="Edited saved sweater title")
    saved_row.get_by_role("button", name="Open").click()

    expect(page.get_by_label("Title")).to_have_value("Edited saved sweater title")
    expect(page.get_by_alt_text("Selected photo 1: sweater.jpg")).to_be_visible()
    expect(page.locator("#current-photo-storage")).not_to_have_text("Current photos: 0 bytes")
    expect(page.locator("#browser-storage-total")).to_contain_text("of 1 GB")

    draft_id = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json()).then((body) => body.drafts[0].id)"
    )
    page.evaluate(
        """async ({ draftId }) => {
          const database = await new Promise((resolve, reject) => {
            const request = indexedDB.open('listing-helper-draft-photos', 2);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          await new Promise((resolve, reject) => {
            const transaction = database.transaction(['photos', 'drafts'], 'readwrite');
            transaction.objectStore('photos').clear();
            transaction.objectStore('drafts').clear();
            const foreignPhoto = new Blob(['not this user'], { type: 'image/jpeg' });
            transaction.objectStore('photos').put({
              accountId: 999,
              draftId,
              position: 1,
              name: 'foreign.jpg',
              type: 'image/jpeg',
              size: foreignPhoto.size,
              lastModified: 0,
              blob: foreignPhoto,
            });
            transaction.objectStore('drafts').put({
              accountId: 999,
              draftId,
              bytes: foreignPhoto.size,
              photoCount: 1,
            });
            transaction.oncomplete = resolve;
            transaction.onerror = () => reject(transaction.error);
          });
        }""",
        {"draftId": draft_id},
    )
    page.reload()
    saved_row = page.locator(".saved-draft-row").filter(has_text="Edited saved sweater title")
    saved_row.get_by_role("button", name="Open").click()
    expect(page.get_by_text("This browser does not have this draft’s photos.")).to_be_visible()
    expect(page.get_by_alt_text("Selected photo 1: foreign.jpg")).to_have_count(0)

    page.on("dialog", lambda dialog: dialog.accept())
    saved_row.get_by_role("button", name="Delete").click()
    expect(page.get_by_role("link", name="Drafts (0)")).to_be_visible()
    expect(page.get_by_text("No saved drafts yet.", exact=False)).to_be_visible()
    expect(page.locator("#result-section")).to_be_hidden()
    expect(page.locator("#current-photo-storage")).to_have_text("Current photos: 0 bytes")


@pytest.mark.browser
def test_text_autosave_updates_photo_revision_without_rewriting_blobs(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    photo_input = page.locator("#photo-input")
    photo_input.set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    if page.get_by_alt_text("Selected photo 1: sweater.jpg").count() == 0:
        photo_input.set_input_files([])
        photo_input.set_input_files(
            {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
        )
    expect(page.get_by_alt_text("Selected photo 1: sweater.jpg")).to_be_visible()
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.get_by_role("button", name="Generate listing")).to_be_enabled()
    page.evaluate(
        """() => {
          window.photoBlobWrites = { puts: 0, deletes: 0 };
          const originalPut = IDBObjectStore.prototype.put;
          const originalDelete = IDBObjectStore.prototype.delete;
          IDBObjectStore.prototype.put = function(value, key) {
            if (this.name === 'photos') window.photoBlobWrites.puts += 1;
            return originalPut.call(this, value, key);
          };
          IDBObjectStore.prototype.delete = function(key) {
            if (this.name === 'photos') window.photoBlobWrites.deletes += 1;
            return originalDelete.call(this, key);
          };
        }"""
    )

    page.get_by_label("Title").fill("Text-only autosave")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    result = page.evaluate(
        """async () => {
          const drafts = await fetch('/api/drafts').then((response) => response.json());
          const session = await fetch('/api/session').then((response) => response.json());
          const serverDraft = await fetch(`/api/drafts/${drafts.drafts[0].id}`)
            .then((response) => response.json());
          const database = await new Promise((resolve, reject) => {
            const request = indexedDB.open('listing-helper-draft-photos', 2);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          const localDraft = await new Promise((resolve, reject) => {
            const transaction = database.transaction('drafts', 'readonly');
            const request = transaction.objectStore('drafts').get([
              session.user.id,
              drafts.drafts[0].id,
            ]);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          return {
            writes: window.photoBlobWrites,
            localRevision: localDraft?.draftRevision,
            serverRevision: serverDraft.revision,
          };
        }"""
    )

    assert result["writes"] == {"puts": 0, "deletes": 0}
    assert result["localRevision"] == result["serverRevision"]
    page.reload()
    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    expect(page.get_by_alt_text("Selected photo 1: sweater.jpg")).to_be_visible()


@pytest.mark.browser
def test_stale_draft_list_does_not_delete_browser_photos(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")

    def return_stale_draft_list(route) -> None:
        route.fulfill(status=200, content_type="application/json", body='{"drafts":[]}')

    page.route("**/api/drafts", return_stale_draft_list)
    page.reload()
    expect(page.get_by_role("link", name="Drafts (0)")).to_be_visible()
    expect(page.locator("#browser-storage-total")).not_to_contain_text("0 bytes of 1 GB")

    page.unroute("**/api/drafts", return_stale_draft_list)
    page.reload()
    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    expect(page.get_by_alt_text("Selected photo 1: sweater.jpg")).to_be_visible()


@pytest.mark.browser
def test_saved_draft_refresh_reads_cross_tab_deletion_tombstones(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    draft_id = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json()).then((data) => data.drafts[0].id)"
    )
    other_tab = page.context.new_page()
    other_tab.goto(live_server_url)
    other_tab.evaluate(
        """async (draftId) => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          await store.markDraftPhotoDeletion(
            database,
            session.user.id,
            draftId,
            'other-tab-token',
          );
        }""",
        draft_id,
    )

    stale_puts = 0

    def count_stale_puts(route) -> None:
        nonlocal stale_puts
        if route.request.method == "PUT":
            stale_puts += 1
        route.continue_()

    page.route("**/api/drafts/*", count_stale_puts)

    page.get_by_label("Title").fill("Cross-tab edit")

    expect(page.get_by_role("link", name="Drafts (0)")).to_be_visible()
    expect(page.locator(".saved-draft-row")).to_have_count(0)
    expect(page.locator("#result-section")).to_be_hidden()
    assert stale_puts == 0
    stored_title = other_tab.evaluate(
        """(draftId) => fetch(`/api/drafts/${draftId}`)
          .then((response) => response.json())
          .then((draft) => draft.draft.title)""",
        draft_id,
    )
    assert stored_title != "Cross-tab edit"
    other_tab.close()


@pytest.mark.browser
def test_saved_draft_open_rechecks_cross_tab_deletion_tombstone(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    draft_id = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json()).then((data) => data.drafts[0].id)"
    )
    page.reload()
    expect(page.locator(".saved-draft-row").get_by_role("button", name="Open")).to_be_visible()
    page.evaluate(
        """(draftId) => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            if (String(resource) !== `/api/drafts/${draftId}` || options?.method) {
              return originalFetch(resource, options);
            }
            return new Promise((resolve) => {
              window.releaseDraftOpen = () => resolve(originalFetch(resource, options));
            });
          };
        }""",
        draft_id,
    )
    other_tab = page.context.new_page()
    other_tab.goto(live_server_url)

    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    page.wait_for_function("typeof window.releaseDraftOpen === 'function'")
    other_tab.evaluate(
        """async (draftId) => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          await store.markDraftPhotoDeletion(
            database,
            session.user.id,
            draftId,
            'open-race-token',
          );
        }""",
        draft_id,
    )
    page.evaluate("window.releaseDraftOpen()")

    expect(
        page.get_by_text("This draft is being deleted in another tab.", exact=False)
    ).to_be_visible()
    expect(page.locator("#result-section")).to_be_hidden()
    expect(page.locator(".saved-draft-row")).to_have_count(0)
    other_tab.close()


@pytest.mark.browser
def test_saved_draft_open_freezes_editor_during_photo_load(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Draft protected during open")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """() => {
          const originalGetAll = IDBIndex.prototype.getAll;
          const originalAddEventListener = IDBRequest.prototype.addEventListener;
          IDBIndex.prototype.getAll = function(...args) {
            const request = originalGetAll.apply(this, args);
            if (this.name === 'accountDraft') request.delayDraftPhotoRead = true;
            return request;
          };
          IDBRequest.prototype.addEventListener = function(type, listener, options) {
            if (!this.delayDraftPhotoRead || type !== 'success') {
              return originalAddEventListener.call(this, type, listener, options);
            }
            const request = this;
            return originalAddEventListener.call(this, type, (event) => {
              window.delayedPhotoReadStarted = true;
              window.releasePhotoRead = () => listener.call(request, event);
            }, options);
          };
        }"""
    )

    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    page.wait_for_function("window.delayedPhotoReadStarted")

    expect(page.get_by_label("Title")).to_be_disabled()
    expect(page.locator(".saved-draft-row").get_by_role("button", name="Open")).to_be_disabled()
    page.evaluate("window.releasePhotoRead()")
    expect(page.get_by_label("Title")).to_be_enabled()
    expect(page.get_by_label("Title")).to_have_value("Draft protected during open")


@pytest.mark.browser
def test_saved_draft_open_rejects_photo_after_delayed_capacity_check(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.get_by_label("Title").fill("Draft A")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.get_by_label("Title").fill("Draft B")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    draft_a_id = page.evaluate(
        """() => fetch('/api/drafts')
          .then((response) => response.json())
          .then((data) => data.drafts.find((draft) => draft.title === 'Draft A').id)"""
    )
    page.evaluate(
        """(draftId) => {
          Object.defineProperty(navigator.storage, 'estimate', {
            configurable: true,
            value: () => new Promise((resolve) => {
              window.releasePhotoCapacityCheck = () => {
                resolve({ quota: 2 * 1024 ** 3, usage: 1024 });
              };
            }),
          });
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            if (String(resource) !== `/api/drafts/${draftId}` || options?.method) {
              return originalFetch(resource, options);
            }
            return new Promise((resolve, reject) => {
              window.releaseDraftGet = () => {
                originalFetch(resource, options).then(resolve, reject);
              };
            });
          };
        }""",
        draft_a_id,
    )

    page.locator("#photo-input").set_input_files(
        {"name": "second.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    page.wait_for_function("typeof window.releasePhotoCapacityCheck === 'function'")
    page.locator(".saved-draft-row").filter(has_text="Draft A").get_by_role(
        "button", name="Open"
    ).click()
    page.wait_for_function("typeof window.releaseDraftGet === 'function'")

    page.evaluate("window.releasePhotoCapacityCheck()")
    expect(page.get_by_text("finish opening", exact=False)).to_be_visible()
    page.evaluate("window.releaseDraftGet()")

    expect(page.get_by_label("Title")).to_have_value("Draft A")
    expect(page.get_by_alt_text("Selected photo 1: first.jpg")).to_be_visible()
    expect(page.get_by_alt_text("Selected photo 2: second.jpg")).to_have_count(0)


@pytest.mark.browser
def test_saved_draft_open_invalidates_pending_ebay_loaders(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Draft A")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          let delayedSuggestion = false;
          let delayedPolicies = false;
          window.putsDuringDraftOpen = 0;
          window.fetch = (resource, options) => {
            const url = String(resource);
            if (window.draftOpenPending && options?.method === 'PUT') {
              window.putsDuringDraftOpen += 1;
            }
            if (!delayedSuggestion && url.startsWith('/api/ebay/categories?q=')) {
              delayedSuggestion = true;
              return originalFetch(resource, options).then((response) => new Promise((resolve) => {
                window.releaseStaleSuggestion = () => resolve(response);
              }));
            }
            if (!delayedPolicies && url === '/api/ebay/business-policies') {
              delayedPolicies = true;
              return originalFetch(resource, options).then((response) => new Promise((resolve) => {
                window.releaseStalePolicies = () => resolve(response);
              }));
            }
            return originalFetch(resource, options);
          };
        }"""
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    page.get_by_label("Title").fill("Draft B")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_role("button", name="Find other categories for this draft").click()
    page.wait_for_function(
        "typeof window.releaseStaleSuggestion === 'function' && "
        "typeof window.releaseStalePolicies === 'function'"
    )
    page.evaluate(
        """() => {
          const originalGetAll = IDBIndex.prototype.getAll;
          const originalAddEventListener = IDBRequest.prototype.addEventListener;
          let delayed = false;
          IDBIndex.prototype.getAll = function(...args) {
            const request = originalGetAll.apply(this, args);
            if (!delayed && this.name === 'accountDraft') {
              delayed = true;
              request.delayDraftPhotoRead = true;
            }
            return request;
          };
          IDBRequest.prototype.addEventListener = function(type, listener, options) {
            if (!this.delayDraftPhotoRead || type !== 'success') {
              return originalAddEventListener.call(this, type, listener, options);
            }
            const request = this;
            return originalAddEventListener.call(this, type, (event) => {
              window.draftOpenPending = true;
              window.releaseDraftPhotoRead = () => listener.call(request, event);
            }, options);
          };
        }"""
    )

    page.locator(".saved-draft-row").filter(has_text="Draft A").get_by_role(
        "button", name="Open"
    ).click()
    page.wait_for_function("window.draftOpenPending")
    page.evaluate("window.releaseStaleSuggestion(); window.releaseStalePolicies()")
    page.wait_for_timeout(200)

    expect(page.get_by_label("eBay category", exact=True)).to_be_disabled()
    expect(page.get_by_label("Shipping policy")).to_be_disabled()
    assert page.evaluate("window.putsDuringDraftOpen") == 0
    page.evaluate("window.releaseDraftPhotoRead(); window.draftOpenPending = false")
    expect(page.get_by_label("Title")).to_have_value("Draft A")
    expect(page.get_by_label("eBay category", exact=True)).to_be_enabled()
    expect(page.get_by_label("Shipping policy")).to_be_enabled()


@pytest.mark.browser
def test_failed_saved_draft_open_restarts_current_loaders(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Open failure draft A")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          let delayedSuggestion = false;
          window.fetch = (resource, options) => {
            const url = String(resource);
            if (!delayedSuggestion && url.startsWith('/api/ebay/categories?q=')) {
              delayedSuggestion = true;
              return originalFetch(resource, options).then((response) => new Promise((resolve) => {
                window.releaseFailedOpenSuggestion = () => resolve(response);
              }));
            }
            return originalFetch(resource, options);
          };
        }"""
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    page.get_by_label("Title").fill("Open failure draft B")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_role("button", name="Find other categories for this draft").click()
    page.wait_for_function("typeof window.releaseFailedOpenSuggestion === 'function'")
    draft_a = page.locator(".saved-draft-row").filter(has_text="Open failure draft A")
    draft_a_id = draft_a.get_attribute("data-draft-id")
    assert draft_a_id
    page.route(
        f"**/api/drafts/{draft_a_id}",
        lambda route: (
            route.fulfill(
                status=503,
                content_type="application/problem+json",
                body='{"detail":"Saved draft read failed."}',
            )
            if route.request.method == "GET"
            else route.continue_()
        ),
    )

    draft_a.get_by_role("button", name="Open").click()

    expect(page.get_by_text("Saved draft read failed.", exact=True)).to_be_visible()
    expect(page.get_by_label("Title")).to_have_value("Open failure draft B")
    expect(page.get_by_label("Title")).to_be_enabled()
    expect(page.get_by_label("eBay category", exact=True)).to_be_enabled()
    expect(page.get_by_label("Shipping policy")).to_be_enabled()
    page.evaluate("window.releaseFailedOpenSuggestion()")
    expect(page.get_by_label("Title")).to_have_value("Open failure draft B")


@pytest.mark.browser
def test_saved_draft_open_invalidates_pending_requirements(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Requirements draft A")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    page.get_by_label("Title").fill("Requirements draft B")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          let delayedRequirements = false;
          window.putsDuringDraftOpen = 0;
          window.fetch = (resource, options) => {
            const url = String(resource);
            if (window.draftOpenPending && options?.method === 'PUT') {
              window.putsDuringDraftOpen += 1;
            }
            if (
              !delayedRequirements &&
              url.includes('/api/ebay/categories/') &&
              url.endsWith('/requirements')
            ) {
              delayedRequirements = true;
              return originalFetch(resource, options).then((response) => new Promise((resolve) => {
                window.releaseStaleRequirements = () => resolve(response);
              }));
            }
            return originalFetch(resource, options);
          };
          const originalGetAll = IDBIndex.prototype.getAll;
          const originalAddEventListener = IDBRequest.prototype.addEventListener;
          let delayedPhotoRead = false;
          IDBIndex.prototype.getAll = function(...args) {
            const request = originalGetAll.apply(this, args);
            if (!delayedPhotoRead && this.name === 'accountDraft') {
              delayedPhotoRead = true;
              request.delayDraftPhotoRead = true;
            }
            return request;
          };
          IDBRequest.prototype.addEventListener = function(type, listener, options) {
            if (!this.delayDraftPhotoRead || type !== 'success') {
              return originalAddEventListener.call(this, type, listener, options);
            }
            const request = this;
            return originalAddEventListener.call(this, type, (event) => {
              window.draftOpenPending = true;
              window.releaseDraftPhotoRead = () => listener.call(request, event);
            }, options);
          };
        }"""
    )
    page.get_by_label("eBay category", exact=True).dispatch_event("change")
    page.wait_for_function("typeof window.releaseStaleRequirements === 'function'")

    page.locator(".saved-draft-row").filter(has_text="Requirements draft A").get_by_role(
        "button", name="Open"
    ).click()
    page.wait_for_function("window.draftOpenPending")
    page.evaluate("window.releaseStaleRequirements()")
    page.wait_for_timeout(200)

    expect(page.get_by_label("eBay condition")).to_be_disabled()
    assert page.evaluate("window.putsDuringDraftOpen") == 0
    page.evaluate("window.releaseDraftPhotoRead(); window.draftOpenPending = false")
    expect(page.get_by_label("Title")).to_have_value("Requirements draft A")
    expect(page.get_by_label("eBay condition")).to_be_enabled()


@pytest.mark.browser
def test_saved_draft_open_defers_late_connection_loaders(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Late connection draft")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.add_init_script(
        """(() => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            if (String(resource) !== '/api/ebay/connection') {
              return originalFetch(resource, options);
            }
            return new Promise((resolve, reject) => {
              window.releaseLateConnection = () => {
                originalFetch(resource, options).then(resolve, reject);
              };
            });
          };
        })()"""
    )
    page.reload()
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    page.evaluate(
        """() => {
          const originalGetAll = IDBIndex.prototype.getAll;
          const originalAddEventListener = IDBRequest.prototype.addEventListener;
          IDBIndex.prototype.getAll = function(...args) {
            const request = originalGetAll.apply(this, args);
            if (this.name === 'accountDraft') request.delayDraftPhotoRead = true;
            return request;
          };
          IDBRequest.prototype.addEventListener = function(type, listener, options) {
            if (!this.delayDraftPhotoRead || type !== 'success') {
              return originalAddEventListener.call(this, type, listener, options);
            }
            const request = this;
            return originalAddEventListener.call(this, type, (event) => {
              window.draftPhotoReadStarted = true;
              window.releaseDraftPhotoRead = () => listener.call(request, event);
            }, options);
          };
        }"""
    )

    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    page.wait_for_function("window.draftPhotoReadStarted")
    page.evaluate("window.releaseLateConnection()")
    expect(page.get_by_text("Connected to browser-seller.")).to_be_visible()

    expect(page.get_by_label("eBay category", exact=True)).to_be_disabled()
    expect(page.get_by_label("Shipping policy")).to_be_disabled()
    page.evaluate("window.releaseDraftPhotoRead()")
    expect(page.get_by_label("Title")).to_have_value("Late connection draft")
    expect(page.get_by_label("eBay category", exact=True)).to_be_enabled()
    expect(page.get_by_label("Shipping policy")).to_be_enabled()


@pytest.mark.browser
def test_saved_draft_open_holds_lock_through_storage_refresh(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Storage draft A")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    page.get_by_label("Title").fill("Storage draft B")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.reload()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    draft_gets: list[str] = []
    page.on(
        "request",
        lambda request: (
            draft_gets.append(request.url)
            if "/api/drafts/" in request.url and request.method == "GET"
            else None
        ),
    )
    page.evaluate(
        """() => {
          const originalGetAll = IDBObjectStore.prototype.getAll;
          const originalAddEventListener = IDBRequest.prototype.addEventListener;
          let draftReads = 0;
          IDBObjectStore.prototype.getAll = function(...args) {
            const request = originalGetAll.apply(this, args);
            if (this.name === 'drafts') {
              draftReads += 1;
              if (draftReads === 5) request.delayFinalStorageRead = true;
            }
            return request;
          };
          IDBRequest.prototype.addEventListener = function(type, listener, options) {
            if (!this.delayFinalStorageRead || type !== 'success') {
              return originalAddEventListener.call(this, type, listener, options);
            }
            const request = this;
            return originalAddEventListener.call(this, type, (event) => {
              window.finalStorageReadStarted = true;
              window.releaseFinalStorageRead = () => listener.call(request, event);
            }, options);
          };
        }"""
    )

    page.locator(".saved-draft-row").filter(has_text="Storage draft A").get_by_role(
        "button", name="Open"
    ).click()
    page.wait_for_function("window.finalStorageReadStarted")

    expect(page.get_by_label("Title")).to_have_value("Storage draft A")
    expect(page.get_by_label("Title")).to_be_disabled()
    expect(page.locator("#photo-input")).to_be_disabled()
    expect(page.get_by_role("button", name="Generate listing")).to_be_disabled()
    open_buttons = page.locator(".saved-draft-row").get_by_role("button", name="Open")
    delete_buttons = page.locator(".saved-draft-row").get_by_role("button", name="Delete")
    expect(open_buttons).to_have_count(2)
    expect(delete_buttons).to_have_count(2)
    for index in range(2):
        expect(open_buttons.nth(index)).to_be_disabled()
        expect(delete_buttons.nth(index)).to_be_disabled()
    page.locator(".saved-draft-row").filter(has_text="Storage draft B").get_by_role(
        "button", name="Open"
    ).dispatch_event("click")
    assert len(draft_gets) == 1

    page.evaluate("window.releaseFinalStorageRead()")
    expect(page.get_by_label("Title")).to_be_enabled()
    expect(page.get_by_label("Title")).to_have_value("Storage draft A")
    for index in range(2):
        expect(open_buttons.nth(index)).to_be_enabled()


@pytest.mark.browser
def test_newer_saved_draft_list_wins_over_delayed_response(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          let delayedList = false;
          window.fetch = (resource, options) => {
            if (!delayedList && resource === '/api/drafts' && !options?.method) {
              delayedList = true;
              return originalFetch(resource, options).then((response) => new Promise((resolve) => {
                window.releaseOldDraftList = () => resolve(response);
              }));
            }
            return originalFetch(resource, options);
          };
        }"""
    )

    page.get_by_label("Title").fill("Older list title")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.wait_for_function("typeof window.releaseOldDraftList === 'function'")
    page.get_by_label("Title").fill("Current list title")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.locator("#saved-drafts-list")).to_contain_text("Current list title")

    page.evaluate("window.releaseOldDraftList()")
    page.wait_for_timeout(200)
    expect(page.locator("#saved-drafts-list")).to_contain_text("Current list title")
    expect(page.locator("#saved-drafts-list")).not_to_contain_text("Older list title")


@pytest.mark.browser
def test_draft_save_stops_when_tombstone_state_cannot_be_read(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    original_title = page.get_by_label("Title").input_value()
    put_attempts = 0

    def count_puts(route) -> None:
        nonlocal put_attempts
        if route.request.method == "PUT":
            put_attempts += 1
        route.continue_()

    page.route("**/api/drafts/*", count_puts)
    page.evaluate(
        """() => {
          const originalGetAll = IDBObjectStore.prototype.getAll;
          IDBObjectStore.prototype.getAll = function(...args) {
            if (this.name === 'drafts') throw new Error('IndexedDB read failed');
            return originalGetAll.apply(this, args);
          };
        }"""
    )

    page.get_by_label("Title").fill("Unsafe stale edit")

    expect(page.locator("#draft-save-status")).to_have_text("Not saved")
    expect(page.get_by_text("could not check pending draft deletions", exact=False)).to_be_visible()
    assert put_attempts == 0
    server_title = page.evaluate(
        """() => fetch('/api/drafts')
          .then((response) => response.json())
          .then((data) => fetch(`/api/drafts/${data.drafts[0].id}`))
          .then((response) => response.json())
          .then((draft) => draft.draft.title)"""
    )
    assert server_title == original_title


@pytest.mark.browser
def test_existing_draft_save_stops_when_indexeddb_cannot_open(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    original_title = page.get_by_label("Title").input_value()
    draft_id = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json()).then((data) => data.drafts[0].id)"
    )
    page.evaluate(
        """async (draftId) => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          await store.markDraftPhotoDeletion(
            database,
            session.user.id,
            draftId,
            'unreadable-tombstone',
          );
        }""",
        draft_id,
    )
    page.add_init_script(
        """(() => {
          Object.defineProperty(indexedDB, 'open', {
            configurable: true,
            value: () => { throw new Error('IndexedDB unavailable'); },
          });
        })()"""
    )
    page.reload()
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    put_attempts = 0

    def count_puts(route) -> None:
        nonlocal put_attempts
        if route.request.method == "PUT":
            put_attempts += 1
        route.continue_()

    page.route("**/api/drafts/*", count_puts)
    page.get_by_label("Title").fill("Unsafe edit without tombstone access")

    expect(page.locator("#draft-save-status")).to_have_text("Not saved")
    expect(page.get_by_text("could not check pending draft deletions", exact=False)).to_be_visible()
    assert put_attempts == 0
    server_title = page.evaluate(
        """(draftId) => fetch(`/api/drafts/${draftId}`)
          .then((response) => response.json())
          .then((draft) => draft.draft.title)""",
        draft_id,
    )
    assert server_title == original_title


@pytest.mark.browser
def test_server_drafts_render_when_tombstone_refresh_fails(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    page.add_init_script(
        """(() => {
          const originalGetAll = IDBObjectStore.prototype.getAll;
          IDBObjectStore.prototype.getAll = function(...args) {
            if (this.name === 'drafts') throw new Error('Tombstone refresh failed');
            return originalGetAll.apply(this, args);
          };
        })()"""
    )

    page.reload()

    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    expect(page.locator(".saved-draft-row").get_by_role("button", name="Open")).to_be_visible()
    expect(page.locator(".saved-draft-row").get_by_role("button", name="Delete")).to_be_visible()
    expect(page.locator("#browser-storage-total")).to_contain_text("unavailable")


@pytest.mark.browser
def test_server_draft_can_be_deleted_when_indexeddb_is_unavailable(
    page: Page, live_server_url: str
) -> None:
    page.add_init_script(
        """(() => {
          Object.defineProperty(indexedDB, 'open', {
            configurable: true,
            value: () => { throw new Error('IndexedDB unavailable'); },
          });
        })()"""
    )
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    delete_attempts = 0

    def count_deletes(route) -> None:
        nonlocal delete_attempts
        if route.request.method == "DELETE":
            delete_attempts += 1
        route.continue_()

    page.route("**/api/drafts/*", count_deletes)
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator(".saved-draft-row").get_by_role("button", name="Delete").click()

    expect(page.get_by_role("link", name="Drafts (0)")).to_be_visible()
    expect(page.get_by_text("server draft was deleted", exact=False)).to_be_visible()
    assert delete_attempts == 1
    assert (
        page.evaluate(
            "fetch('/api/drafts').then((response) => response.json())"
            ".then((data) => data.drafts.length)"
        )
        == 0
    )


@pytest.mark.browser
def test_server_draft_can_be_deleted_after_runtime_indexeddb_failure(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    delete_attempts = 0

    def count_deletes(route) -> None:
        nonlocal delete_attempts
        if route.request.method == "DELETE":
            delete_attempts += 1
        route.continue_()

    page.route("**/api/drafts/*", count_deletes)
    page.evaluate(
        """() => {
          const original = IDBDatabase.prototype.transaction;
          IDBDatabase.prototype.transaction = function(storeNames, mode, options) {
            const names = Array.isArray(storeNames) ? storeNames : [storeNames];
            if (mode === 'readwrite' && names.includes('drafts')) {
              throw new Error('IndexedDB became unavailable');
            }
            return original.call(this, storeNames, mode, options);
          };
        }"""
    )
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator(".saved-draft-row").get_by_role("button", name="Delete").click()

    expect(page.get_by_role("link", name="Drafts (0)")).to_be_visible()
    expect(page.get_by_text("server draft was deleted", exact=False)).to_be_visible()
    assert delete_attempts == 1
    assert (
        page.evaluate(
            "fetch('/api/drafts').then((response) => response.json())"
            ".then((data) => data.drafts.length)"
        )
        == 0
    )


@pytest.mark.browser
def test_generation_continues_after_runtime_indexeddb_failure(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    generation_attempts = 0

    def count_generations(route) -> None:
        nonlocal generation_attempts
        generation_attempts += 1
        route.continue_()

    page.route("**/api/listings/generate", count_generations)
    page.evaluate(
        """() => {
          IDBDatabase.prototype.transaction = function() {
            throw new Error('IndexedDB became unavailable');
          };
        }"""
    )

    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.locator("#result-section")).to_be_visible()
    expect(page.locator("#browser-storage-total")).to_contain_text("unavailable")
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    expect(page.locator(".saved-draft-row").get_by_role("button", name="Delete")).to_be_visible()
    assert generation_attempts == 1
    assert (
        page.evaluate(
            "fetch('/api/drafts').then((response) => response.json())"
            ".then((data) => data.drafts.length)"
        )
        == 1
    )


@pytest.mark.browser
def test_generation_continues_when_prior_draft_photo_save_is_incomplete(
    page: Page, live_server_url: str
) -> None:
    generation_attempts = 0

    def count_generations(route) -> None:
        nonlocal generation_attempts
        generation_attempts += 1
        route.continue_()

    page.route("**/api/listings/generate", count_generations)
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Prior saved draft")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """() => {
          const original = IDBDatabase.prototype.transaction;
          IDBDatabase.prototype.transaction = function(storeNames, mode, options) {
            const names = Array.isArray(storeNames) ? storeNames : [storeNames];
            if (mode === 'readwrite' && names.includes('photos')) {
              throw new DOMException('Photo storage failed', 'UnknownError');
            }
            return original.call(this, storeNames, mode, options);
          };
        }"""
    )
    page.locator("#photo-input").set_input_files(
        {"name": "second.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.get_by_text("could not save its photos", exact=False)).to_be_visible()

    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    expect(page.locator("#result-section")).to_be_visible()
    expect(page.locator("#saved-drafts-list")).to_contain_text("Prior saved draft")
    expect(page.get_by_text("Save the current draft before", exact=False)).to_have_count(0)
    assert generation_attempts == 2


@pytest.mark.browser
def test_generation_continues_when_prior_draft_autosave_fails(
    page: Page, live_server_url: str
) -> None:
    generation_attempts = 0

    def count_generations(route) -> None:
        nonlocal generation_attempts
        generation_attempts += 1
        route.continue_()

    page.route("**/api/listings/generate", count_generations)
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.wait_for_timeout(800)

    def reject_draft_save(route) -> None:
        if route.request.method == "PUT":
            route.fulfill(
                status=503,
                content_type="application/problem+json",
                body='{"detail":"Draft storage is unavailable."}',
            )
            return
        route.continue_()

    page.route("**/api/drafts/*", reject_draft_save)
    page.get_by_label("Title").fill("Edit that did not save")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    expect(page.locator("#result-section")).to_be_visible()
    expect(page.get_by_text("Save the current draft before", exact=False)).to_have_count(0)
    stored_titles = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json())"
        ".then((data) => data.drafts.map((draft) => draft.title))"
    )
    assert "Edit that did not save" not in stored_titles
    assert len(stored_titles) == 2
    assert generation_attempts == 2


@pytest.mark.browser
def test_cross_tab_draft_deletion_still_blocks_generation(page: Page, live_server_url: str) -> None:
    generation_attempts = 0

    def count_generations(route) -> None:
        nonlocal generation_attempts
        generation_attempts += 1
        route.continue_()

    page.route("**/api/listings/generate", count_generations)
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    draft_id = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json()).then((data) => data.drafts[0].id)"
    )
    page.evaluate(
        """async (draftId) => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          await store.markDraftPhotoDeletion(
            database,
            session.user.id,
            draftId,
            'generation-race-token',
          );
        }""",
        draft_id,
    )

    page.evaluate("document.querySelector('#listing-form').requestSubmit()")

    expect(page.locator("#error-message")).to_contain_text(
        "This draft is being deleted in another tab."
    )
    expect(page.locator("#result-section")).to_be_hidden()
    assert generation_attempts == 1


@pytest.mark.browser
def test_stale_tab_cannot_overwrite_a_newer_draft_revision(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.wait_for_timeout(800)
    other_tab = page.context.new_page()
    other_tab.goto(live_server_url)
    other_tab.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    expect(other_tab.locator("#draft-save-status")).to_have_text("Saved")
    page.reload()
    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")

    page.get_by_label("Title").fill("Newer tab title")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    other_tab.get_by_label("Title").fill("Stale tab title")

    expect(other_tab.locator("#draft-save-status")).to_have_text("Not saved")
    expect(other_tab.get_by_text("changed in another tab", exact=False)).to_be_visible()
    stored_title = page.evaluate(
        """() => fetch('/api/drafts')
          .then((response) => response.json())
          .then((data) => fetch(`/api/drafts/${data.drafts[0].id}`))
          .then((response) => response.json())
          .then((draft) => draft.draft.title)"""
    )
    assert stored_title == "Newer tab title"
    other_tab.close()


@pytest.mark.browser
def test_draft_saves_serialize_before_tombstone_check(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    put_bodies: list[dict[str, object]] = []

    def capture_puts(route) -> None:
        if route.request.method == "PUT":
            put_bodies.append(route.request.post_data_json)
        route.continue_()

    page.route("**/api/drafts/*", capture_puts)
    page.evaluate(
        """() => {
          window.delayedDraftReadStarted = false;
          const original = IDBRequest.prototype.addEventListener;
          IDBRequest.prototype.addEventListener = function(type, listener, options) {
            if (type === 'success' && this.source?.name === 'drafts') {
              return original.call(this, type, (event) => {
                window.delayedDraftReadStarted = true;
                setTimeout(() => listener.call(this, event), 250);
              }, options);
            }
            return original.call(this, type, listener, options);
          };
        }"""
    )

    page.get_by_label("Title").fill("Older queued title")
    page.evaluate("document.querySelector('#save-draft-button').click()")
    page.wait_for_function("window.delayedDraftReadStarted")
    page.get_by_label("Title").fill("Newest queued title")
    page.evaluate("document.querySelector('#save-draft-button').click()")

    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    assert len(put_bodies) == 1
    assert put_bodies[0]["draft"]["title"] == "Newest queued title"
    server_title = page.evaluate(
        """() => fetch('/api/drafts')
          .then((response) => response.json())
          .then((data) => fetch(`/api/drafts/${data.drafts[0].id}`))
          .then((response) => response.json())
          .then((draft) => draft.draft.title)"""
    )
    assert server_title == "Newest queued title"


@pytest.mark.browser
def test_retryable_delete_failure_keeps_cleanup_pending(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    delete_attempts = 0

    def fail_first_delete(route) -> None:
        nonlocal delete_attempts
        if route.request.method != "DELETE":
            route.continue_()
            return
        delete_attempts += 1
        if delete_attempts <= 2:
            route.fulfill(
                status=503,
                content_type="application/problem+json",
                body='{"detail":"The draft store is temporarily unavailable."}',
            )
        else:
            route.continue_()

    page.route("**/api/drafts/*", fail_first_delete)
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator(".saved-draft-row").get_by_role("button", name="Delete").click()

    expect(page.get_by_text("will retry the draft and photo cleanup", exact=False)).to_be_visible()
    expect(page.locator("#result-section")).to_be_hidden()
    expect(page.get_by_role("link", name="Drafts (0)")).to_be_visible()
    expect(page.get_by_label("Title")).to_be_hidden()
    pending_count = page.evaluate(
        """async () => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          return (await store.pendingDraftPhotoDeletions(database, session.user.id)).length;
        }"""
    )
    assert pending_count == 1

    page.reload()
    expect(page.get_by_role("link", name="Drafts (0)")).to_be_visible()
    expect(page.locator("#result-section")).to_be_hidden()
    expect(page.locator("#browser-storage-total")).not_to_contain_text("0 bytes of 1 GB")
    assert delete_attempts == 2

    page.reload()
    expect(page.get_by_role("link", name="Drafts (0)")).to_be_visible()
    expect(page.locator("#browser-storage-total")).to_contain_text("0 bytes of 1 GB")
    assert delete_attempts == 3


@pytest.mark.browser
def test_startup_clears_tombstone_after_definite_delete_rejection(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    draft_id = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json()).then((data) => data.drafts[0].id)"
    )
    page.evaluate(
        """async (draftId) => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          await store.markDraftPhotoDeletion(
            database,
            session.user.id,
            draftId,
            'startup-retry-token',
          );
        }""",
        draft_id,
    )
    delete_attempts = 0

    def reject_delete(route) -> None:
        nonlocal delete_attempts
        if route.request.method != "DELETE":
            route.continue_()
            return
        delete_attempts += 1
        route.fulfill(
            status=409,
            content_type="application/problem+json",
            body=(
                '{"code":"draft_publication_in_progress","detail":"Publication is in progress."}'
            ),
        )

    page.route("**/api/drafts/*", reject_delete)
    page.reload()

    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    expect(page.get_by_alt_text("Selected photo 1: sweater.jpg")).to_be_visible()
    assert delete_attempts == 1


@pytest.mark.browser
def test_current_draft_is_frozen_before_deletion_cleanup(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.wait_for_timeout(800)
    put_attempts = 0

    def hold_delete(route) -> None:
        nonlocal put_attempts
        if route.request.method == "PUT":
            put_attempts += 1
            route.continue_()
            return
        if route.request.method == "DELETE":
            return
        route.continue_()

    page.route("**/api/drafts/*", hold_delete)
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator(".saved-draft-row").get_by_role("button", name="Delete").click()

    expect(page.get_by_label("Title")).to_be_disabled()
    page.get_by_label("Title").evaluate(
        """(field) => {
          field.value = 'Late deletion edit';
          field.dispatchEvent(new Event('input', { bubbles: true }));
        }"""
    )
    page.wait_for_timeout(800)

    assert put_attempts == 0


@pytest.mark.browser
def test_rejected_current_draft_deletion_requeues_canceled_autosave(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          const originalSetTimeout = window.setTimeout.bind(window);
          window.autosaveTimerCalls = 0;
          window.setTimeout = (callback, delay, ...args) => {
            if (delay === 600) {
              window.autosaveTimerCalls += 1;
              delay = window.autosaveTimerCalls === 1 ? 10000 : 0;
            }
            return originalSetTimeout(callback, delay, ...args);
          };
          window.fetch = (resource, options) => {
            if (!String(resource).startsWith('/api/drafts/') || options?.method !== 'DELETE') {
              return originalFetch(resource, options);
            }
            return new Promise((resolve) => {
              window.rejectDelete = () => resolve(new Response(
                JSON.stringify({ detail: 'Simulated deletion rejection.' }),
                { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
              ));
            });
          };
        }"""
    )
    page.on("dialog", lambda dialog: dialog.accept())

    page.get_by_label("Title").fill("Edit preserved after failed deletion")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    page.locator(".saved-draft-row").get_by_role("button", name="Delete").click()
    page.wait_for_function("typeof window.rejectDelete === 'function'")
    page.evaluate("window.rejectDelete()")

    expect(page.get_by_text("Simulated deletion rejection.")).to_be_visible()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    assert page.evaluate("window.autosaveTimerCalls") == 2
    server_title = page.evaluate(
        """() => fetch('/api/drafts')
          .then((response) => response.json())
          .then((data) => fetch(`/api/drafts/${data.drafts[0].id}`))
          .then((response) => response.json())
          .then((draft) => draft.draft.title)"""
    )
    assert server_title == "Edit preserved after failed deletion"


@pytest.mark.browser
def test_rejected_deletion_restores_draft_after_tombstone_write_failure(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          const originalSetTimeout = window.setTimeout.bind(window);
          const originalTransaction = IDBDatabase.prototype.transaction;
          let failedTombstoneWrite = false;
          window.autosaveTimerCalls = 0;
          window.setTimeout = (callback, delay, ...args) => {
            if (delay === 600) {
              window.autosaveTimerCalls += 1;
              delay = window.autosaveTimerCalls === 1 ? 10000 : 0;
            }
            return originalSetTimeout(callback, delay, ...args);
          };
          IDBDatabase.prototype.transaction = function(storeNames, mode, options) {
            const names = Array.isArray(storeNames) ? storeNames : [storeNames];
            if (mode === 'readwrite' && names.length === 1 && names[0] === 'drafts') {
              if (!failedTombstoneWrite) {
                failedTombstoneWrite = true;
                throw new DOMException('Tombstone write failed', 'UnknownError');
              }
            }
            return originalTransaction.call(this, storeNames, mode, options);
          };
          window.fetch = (resource, options) => {
            if (!String(resource).startsWith('/api/drafts/') || options?.method !== 'DELETE') {
              return originalFetch(resource, options);
            }
            return new Promise((resolve) => {
              window.rejectDeleteWithoutTombstone = () => resolve(new Response(
                JSON.stringify({ detail: 'Simulated deletion rejection.' }),
                { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
              ));
            });
          };
        }"""
    )
    page.on("dialog", lambda dialog: dialog.accept())

    page.get_by_label("Title").fill("Edit restored without tombstone")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    page.locator(".saved-draft-row").get_by_role("button", name="Delete").click()
    page.wait_for_function("typeof window.rejectDeleteWithoutTombstone === 'function'")
    page.evaluate("window.rejectDeleteWithoutTombstone()")

    expect(page.get_by_text("Simulated deletion rejection.")).to_be_visible()
    expect(page.get_by_label("Title")).to_be_enabled()
    expect(page.get_by_label("Title")).to_have_value("Edit restored without tombstone")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    assert page.evaluate("window.autosaveTimerCalls") == 2
    server_title = page.evaluate(
        """() => fetch('/api/drafts')
          .then((response) => response.json())
          .then((data) => fetch(`/api/drafts/${data.drafts[0].id}`))
          .then((response) => response.json())
          .then((draft) => draft.draft.title)"""
    )
    assert server_title == "Edit restored without tombstone"


@pytest.mark.browser
def test_current_draft_deletion_blocks_dropped_photos(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            if (!String(resource).startsWith('/api/drafts/') || options?.method !== 'DELETE') {
              return originalFetch(resource, options);
            }
            return new Promise((resolve) => {
              window.rejectDelete = () => resolve(new Response(
                JSON.stringify({ detail: 'Simulated deletion rejection.' }),
                { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
              ));
            });
          };
        }"""
    )
    page.on("dialog", lambda dialog: dialog.accept())

    page.locator(".saved-draft-row").get_by_role("button", name="Delete").click()
    page.wait_for_function("typeof window.rejectDelete === 'function'")
    page.evaluate(
        """() => {
          const transfer = new DataTransfer();
          transfer.items.add(new File(
            [new Uint8Array(10)],
            'dropped.jpg',
            { type: 'image/jpeg' },
          ));
          document.querySelector('#drop-zone').dispatchEvent(new DragEvent('drop', {
            bubbles: true,
            cancelable: true,
            dataTransfer: transfer,
          }));
        }"""
    )

    expect(
        page.get_by_text("Wait for draft deletion to finish before you change photos.")
    ).to_be_visible()
    expect(page.get_by_alt_text("Selected photo 2: dropped.jpg")).to_have_count(0)
    page.evaluate("window.rejectDelete()")
    expect(page.get_by_text("Simulated deletion rejection.")).to_be_visible()
    expect(page.get_by_alt_text("Selected photo 1: sweater.jpg")).to_be_visible()
    expect(page.get_by_alt_text("Selected photo 2: dropped.jpg")).to_have_count(0)
    expect(page.get_by_label("Title")).to_be_enabled()


@pytest.mark.browser
def test_failed_local_photo_deletion_retries_after_reload(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    draft_id = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json()).then((data) => data.drafts[0].id)"
    )

    page.evaluate(
        """() => {
          const original = IDBDatabase.prototype.transaction;
          IDBDatabase.prototype.transaction = function(storeNames, mode, options) {
            if (
              mode === 'readwrite' &&
              Array.isArray(storeNames) &&
              storeNames.includes('photos')
            ) {
              throw new Error('simulated cleanup failure');
            }
            return original.call(this, storeNames, mode, options);
          };
          const originalFetch = window.fetch.bind(window);
          window.deletionIntentSeen = false;
          window.fetch = async (resource, options) => {
            if (String(resource).startsWith('/api/drafts/') && options?.method === 'DELETE') {
              const store = await import('/static/photo-store.js');
              const session = await originalFetch('/api/session')
                .then((response) => response.json());
              const database = await store.openPhotoStore();
              const pending = await store.pendingDraftPhotoDeletions(
                database,
                session.user.id,
              );
              window.deletionIntentSeen = pending.length === 1;
            }
            return originalFetch(resource, options);
          };
        }"""
    )
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator(".saved-draft-row").get_by_role("button", name="Delete").click()
    expect(page.get_by_text("could not remove its local photos", exact=False)).to_be_visible()
    assert page.evaluate("window.deletionIntentSeen") is True
    expect(page.locator("#browser-storage-total")).not_to_contain_text("0 bytes of 1 GB")

    page.reload()
    expect(page.get_by_role("link", name="Drafts (0)")).to_be_visible()
    expect(page.locator("#browser-storage-total")).to_contain_text("0 bytes of 1 GB")
    stale_write = page.evaluate(
        """async (draftId) => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          const file = new File([new Uint8Array(10)], 'stale.jpg', { type: 'image/jpeg' });
          try {
            await store.clearDraftPhotoDeletion(
              database,
              session.user.id,
              draftId,
              'older-failed-delete',
            );
            await store.saveDraftPhotos(database, session.user.id, draftId, [file], 2);
            return { error: null };
          } catch (error) {
            const stats = await store.photoStorageStats(database, session.user.id);
            return { error: error.name, totalBytes: stats.totalBytes };
          }
        }""",
        draft_id,
    )
    assert stale_write == {"error": "DeletedDraftPhotoWriteError", "totalBytes": 0}


@pytest.mark.browser
def test_failed_delete_keeps_a_newer_tabs_cleanup_intent(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    draft_id = page.evaluate(
        "fetch('/api/drafts').then((response) => response.json()).then((data) => data.drafts[0].id)"
    )
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            if (!String(resource).startsWith('/api/drafts/') || options?.method !== 'DELETE') {
              return originalFetch(resource, options);
            }
            return new Promise((resolve) => {
              window.failDelete = () => resolve(new Response(
                JSON.stringify({ detail: 'Simulated deletion rejection.' }),
                { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
              ));
            });
          };
        }"""
    )
    page.on("dialog", lambda dialog: dialog.accept())

    page.locator(".saved-draft-row").get_by_role("button", name="Delete").click()
    page.wait_for_function("typeof window.failDelete === 'function'")
    old_token = page.evaluate(
        """async () => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          return (await store.pendingDraftPhotoDeletions(database, session.user.id))[0].token;
        }"""
    )
    page.evaluate(
        """async (draftId) => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          await store.markDraftPhotoDeletion(
            database,
            session.user.id,
            draftId,
            'newer-tab-token',
          );
          window.failDelete();
        }""",
        draft_id,
    )

    expect(
        page.get_by_text("Another deletion attempt is still pending.", exact=False)
    ).to_be_visible()
    remaining = page.evaluate(
        """async () => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          return (await store.pendingDraftPhotoDeletions(database, session.user.id))[0];
        }"""
    )
    assert old_token != "newer-tab-token"
    assert remaining["draftId"] == draft_id
    assert remaining["token"] == "newer-tab-token"
    stale_write = page.evaluate(
        """async (draftId) => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          const file = new File([new Uint8Array(10)], 'stale.jpg', { type: 'image/jpeg' });
          try {
            await store.saveDraftPhotos(database, session.user.id, draftId, [file], 2);
            return null;
          } catch (error) {
            return error.name;
          }
        }""",
        draft_id,
    )
    assert stale_write == "DeletedDraftPhotoWriteError"


@pytest.mark.browser
def test_partial_photo_recovery_keeps_saved_expected_count(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        [
            {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()},
            {"name": "second.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()},
        ]
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Recovery draft A")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    page.get_by_label("Title").fill("Recovery draft B")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """async () => {
          const database = await new Promise((resolve, reject) => {
            const request = indexedDB.open('listing-helper-draft-photos', 2);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          await new Promise((resolve, reject) => {
            const transaction = database.transaction(['photos', 'drafts'], 'readwrite');
            transaction.objectStore('photos').clear();
            transaction.objectStore('drafts').clear();
            transaction.oncomplete = resolve;
            transaction.onerror = () => reject(transaction.error);
          });
        }"""
    )
    page.reload()
    page.locator(".saved-draft-row").filter(has_text="Recovery draft A").get_by_role(
        "button", name="Open"
    ).click()
    expect(page.get_by_text("does not have this draft’s photos", exact=False)).to_be_visible()
    page.locator("#photo-input").set_input_files(
        {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    expect(page.locator("#photo-reselect-message")).to_contain_text("Select all 2 original photos")
    expect(page.locator("#ebay-publish-readiness")).to_contain_text("Select all 2 original photos.")
    page.wait_for_timeout(800)
    expect(page.locator("#saved-drafts-list")).to_contain_text("2 photos")

    page.locator(".saved-draft-row").filter(has_text="Recovery draft B").get_by_role(
        "button", name="Open"
    ).click()

    expect(page.get_by_label("Title")).to_have_value("Recovery draft A")
    expect(page.get_by_alt_text("Selected photo 1: first.jpg")).to_be_visible()
    expect(page.get_by_text("Save the current draft before", exact=False)).to_be_visible()

    page.locator("#photo-input").set_input_files(
        {"name": "second.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.locator("#photo-reselect-message")).to_be_hidden()


@pytest.mark.browser
def test_clean_photo_missing_draft_can_open_another_draft(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "common.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Draft A")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.get_by_label("Title").fill("Draft B")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate(
        """async () => {
          const database = await new Promise((resolve, reject) => {
            const request = indexedDB.open('listing-helper-draft-photos', 2);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          await new Promise((resolve, reject) => {
            const transaction = database.transaction(['photos', 'drafts'], 'readwrite');
            transaction.objectStore('photos').clear();
            transaction.objectStore('drafts').clear();
            transaction.oncomplete = resolve;
            transaction.onerror = () => reject(transaction.error);
          });
        }"""
    )
    page.add_init_script(
        """(() => {
          Object.defineProperty(indexedDB, 'open', {
            configurable: true,
            value: () => { throw new Error('IndexedDB unavailable'); },
          });
        })()"""
    )
    page.route("**/api/ebay/categories**", lambda route: route.abort())
    page.reload()

    put_attempts = 0

    def count_puts(route) -> None:
        nonlocal put_attempts
        if route.request.method == "PUT":
            put_attempts += 1
        route.continue_()

    page.route("**/api/drafts/*", count_puts)

    page.locator(".saved-draft-row").filter(has_text="Draft A").get_by_role(
        "button", name="Open"
    ).click()
    expect(page.get_by_text("does not have this draft’s photos", exact=False)).to_be_visible()
    page.locator(".saved-draft-row").filter(has_text="Draft B").get_by_role(
        "button", name="Open"
    ).click()

    expect(page.get_by_label("Title")).to_have_value("Draft B")
    assert put_attempts == 0


@pytest.mark.browser
def test_saved_photo_edits_persist_in_order(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        [
            {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()},
            {"name": "second.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()},
        ]
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")

    page.get_by_role("button", name="Move photo 2 earlier").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_role("button", name="Remove photo 2").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.locator("#photo-input").set_input_files(
        {"name": "third.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#photo-list img").nth(1)).to_have_attribute(
        "alt", "Selected photo 2: third.jpg"
    )
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.locator("#saved-drafts-list")).to_contain_text("2 photos")

    page.reload()
    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    previews = page.locator("#photo-list img")
    expect(previews).to_have_count(2)
    expect(previews.nth(0)).to_have_attribute("alt", "Selected photo 1: second.jpg")
    expect(previews.nth(1)).to_have_attribute("alt", "Selected photo 2: third.jpg")


@pytest.mark.browser
def test_failed_photo_persistence_blocks_draft_navigation(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "common.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Draft A")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    page.get_by_label("Title").fill("Draft B")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")

    draft_a = page.locator(".saved-draft-row").filter(has_text="Draft A")
    draft_b = page.locator(".saved-draft-row").filter(has_text="Draft B")
    draft_a.get_by_role("button", name="Open").click()
    expect(page.get_by_label("Title")).to_have_value("Draft A")
    page.evaluate(
        """() => {
          const original = IDBDatabase.prototype.transaction;
          IDBDatabase.prototype.transaction = function(storeNames, mode, options) {
            const names = Array.isArray(storeNames) ? storeNames : [storeNames];
            if (mode === 'readwrite' && names.includes('photos')) {
              throw new DOMException('Photo storage failed', 'UnknownError');
            }
            return original.call(this, storeNames, mode, options);
          };
        }"""
    )
    page.locator("#photo-input").set_input_files(
        {"name": "private-a.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.get_by_text("could not save its photos", exact=False)).to_be_visible()

    draft_b.get_by_role("button", name="Open").click()

    expect(page.get_by_text("Save the current draft before", exact=False)).to_be_visible()
    expect(page.get_by_label("Title")).to_have_value("Draft A")
    expect(page.get_by_alt_text("Selected photo 2: private-a.jpg")).to_be_visible()


@pytest.mark.browser
def test_photo_change_during_browser_write_persists_new_revision(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        [
            {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()},
            {"name": "second.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()},
        ]
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.get_by_role("button", name="Generate listing")).to_be_enabled()
    page.evaluate(
        """() => {
          const originalSetTimeout = window.setTimeout.bind(window);
          window.setTimeout = (handler, delay, ...args) =>
            originalSetTimeout(handler, delay === 600 ? 0 : delay, ...args);
          const original = IDBObjectStore.prototype.put;
          let changed = false;
          IDBObjectStore.prototype.put = function(value, key) {
            const request = original.call(this, value, key);
            if (!changed && this.name === 'photos') {
              changed = true;
              document.querySelector('[aria-label="Move photo 3 earlier"]').click();
            }
            return request;
          };
        }"""
    )
    page.locator("#photo-input").set_input_files(
        {"name": "third.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    page.evaluate("document.querySelector('#save-draft-button').click()")
    expect(page.locator("#photo-list img").nth(1)).to_have_attribute(
        "alt", "Selected photo 2: third.jpg"
    )
    expect(page.locator("#draft-save-status")).to_have_text("Saved")

    page.reload()
    page.locator(".saved-draft-row").get_by_role("button", name="Open").click()
    previews = page.locator("#photo-list img")
    expect(previews).to_have_count(3)
    expect(previews.nth(1)).to_have_attribute("alt", "Selected photo 2: third.jpg")
    expect(previews.nth(2)).to_have_attribute("alt", "Selected photo 3: second.jpg")


@pytest.mark.browser
def test_delayed_photo_addition_cannot_cross_drafts(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "common.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_label("Title").fill("Draft A")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    page.get_by_label("Title").fill("Draft B")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.reload()
    expect(page.locator("#saved-drafts-list")).to_contain_text("Draft A")
    expect(page.locator("#saved-drafts-list")).to_contain_text("Draft B")

    draft_a = page.locator(".saved-draft-row").filter(has_text="Draft A")
    draft_b = page.locator(".saved-draft-row").filter(has_text="Draft B")
    draft_a.get_by_role("button", name="Open").click()
    expect(page.get_by_label("Title")).to_have_value("Draft A")
    expect(page.get_by_alt_text("Selected photo 1: common.jpg")).to_be_visible()
    page.evaluate(
        """() => {
          Object.defineProperty(navigator.storage, 'estimate', {
            configurable: true,
            value: () => new Promise((resolve) => { window.resolveStorageEstimate = resolve; }),
          });
        }"""
    )
    page.locator("#photo-input").set_input_files(
        {"name": "private-a.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    draft_b.get_by_role("button", name="Open").click()
    expect(page.get_by_label("Title")).to_have_value("Draft B")
    page.evaluate("window.resolveStorageEstimate({ quota: 2 * 1024 ** 3, usage: 1024 })")

    expect(page.get_by_alt_text("Selected photo 2: private-a.jpg")).to_have_count(0)
    expect(page.get_by_text("open draft changed", exact=False)).to_be_visible()


@pytest.mark.browser
def test_generation_freezes_context_until_unsaved_result_is_shown(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator("#account-email")).to_have_text("owner@example.com")
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            if (String(resource).startsWith('/api/drafts/') && options?.method === 'PUT') {
              return Promise.resolve(new Response(
                JSON.stringify({ detail: 'Draft storage is unavailable.' }),
                { status: 503, headers: { 'Content-Type': 'application/json' } },
              ));
            }
            if (resource !== '/api/listings/generate') return originalFetch(resource, options);
            return new Promise((resolve, reject) => {
              window.releaseGeneration = () => {
                originalFetch(resource, options).then(async (response) => {
                  const body = await response.json();
                  body.saved = false;
                  resolve(new Response(JSON.stringify(body), {
                    status: response.status,
                    headers: { 'Content-Type': 'application/json' },
                  }));
                }, reject);
              };
            });
          };
        }"""
    )
    page.locator("#photo-input").set_input_files(
        {"name": "submitted.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.wait_for_function("typeof window.releaseGeneration === 'function'")
    expect(page.locator("#photo-input")).to_be_disabled()
    page.evaluate("window.releaseGeneration()")

    expect(page.locator("#result-section")).to_be_visible()
    expect(page.locator("#draft-save-status")).to_have_text("Not saved")
    expect(page.get_by_alt_text("Selected photo 1: submitted.jpg")).to_be_visible()


@pytest.mark.browser
def test_generation_discards_delayed_category_requirements(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_text("Connected to browser-seller.")).to_be_visible()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    expect(page.get_by_label("eBay category", exact=True)).to_have_value("175786")
    expect(page.get_by_label("eBay condition")).to_have_value("3000")
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          let delayedRequirements = false;
          window.putDuringGeneration = false;
          window.fetch = (resource, options) => {
            const url = String(resource);
            if (
              !delayedRequirements &&
              url.includes('/api/ebay/categories/') &&
              url.endsWith('/requirements')
            ) {
              delayedRequirements = true;
              return new Promise((resolve, reject) => {
                window.releaseRequirements = () => {
                  originalFetch(resource, options).then(resolve, reject);
                };
              });
            }
            if (resource === '/api/listings/generate') {
              window.generationPending = true;
              return new Promise((resolve, reject) => {
                window.releaseGeneration = () => {
                  originalFetch(resource, options).then(resolve, reject);
                };
              });
            }
            if (
              window.generationPending &&
              url.startsWith('/api/drafts/') &&
              options?.method === 'PUT'
            ) {
              window.putDuringGeneration = true;
            }
            return originalFetch(resource, options);
          };
        }"""
    )

    page.get_by_label("eBay category", exact=True).dispatch_event("change")
    page.wait_for_function("typeof window.releaseRequirements === 'function'")
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.wait_for_function("typeof window.releaseGeneration === 'function'")
    expect(page.get_by_label("eBay condition")).to_be_disabled()
    page.evaluate("window.releaseRequirements()")
    page.wait_for_timeout(800)

    expect(page.get_by_label("eBay condition")).to_be_disabled()
    assert page.evaluate("window.putDuringGeneration") is False
    page.evaluate("window.releaseGeneration()")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")


@pytest.mark.browser
def test_concurrent_browser_writes_enforce_one_profile_limit(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator("#account-email")).to_have_text("owner@example.com")
    result = page.evaluate(
        """async () => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          await new Promise((resolve, reject) => {
            const transaction = database.transaction('drafts', 'readwrite');
            transaction.objectStore('drafts').put({
              accountId: 999,
              draftId: 'existing',
              bytes: store.PHOTO_STORAGE_LIMIT - 100,
              photoCount: 1,
            });
            transaction.oncomplete = resolve;
            transaction.onerror = () => reject(transaction.error);
          });
          const file = new File([new Uint8Array(60)], 'small.jpg', { type: 'image/jpeg' });
          const writes = await Promise.allSettled([
            store.saveDraftPhotos(
              database, session.user.id, 'race-a', [file], 1
            ),
            store.saveDraftPhotos(
              database, session.user.id, 'race-b', [file], 1
            ),
          ]);
          const stats = await store.photoStorageStats(database, session.user.id);
          return {
            statuses: writes.map((write) => write.status),
            totalBytes: stats.totalBytes,
            limit: store.PHOTO_STORAGE_LIMIT,
          };
        }"""
    )

    assert sorted(result["statuses"]) == ["fulfilled", "rejected"]
    assert result["totalBytes"] <= result["limit"]


@pytest.mark.browser
def test_concurrent_deletion_intents_remain_distinct(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator("#account-email")).to_have_text("owner@example.com")

    pending = page.evaluate(
        """async () => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const [firstDatabase, secondDatabase] = await Promise.all([
            store.openPhotoStore(),
            store.openPhotoStore(),
          ]);
          await Promise.all([
            store.markDraftPhotoDeletion(
              firstDatabase, session.user.id, 'first-draft', 'first-token'
            ),
            store.markDraftPhotoDeletion(
              secondDatabase, session.user.id, 'second-draft', 'second-token'
            ),
          ]);
          const records = await store.pendingDraftPhotoDeletions(
            firstDatabase,
            session.user.id,
          );
          return records.sort((left, right) => left.draftId.localeCompare(right.draftId));
        }"""
    )

    assert pending == [
        {"accountId": 1, "draftId": "first-draft", "token": "first-token"},
        {"accountId": 1, "draftId": "second-draft", "token": "second-token"},
    ]


@pytest.mark.browser
def test_older_server_revision_cannot_replace_newer_local_photos(
    page: Page, live_server_url: str
) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator("#account-email")).to_have_text("owner@example.com")

    result = page.evaluate(
        """async () => {
          const store = await import('/static/photo-store.js');
          const session = await fetch('/api/session').then((response) => response.json());
          const database = await store.openPhotoStore();
          const newer = new File(['new'], 'newer.jpg', { type: 'image/jpeg' });
          const older = new File(['old'], 'older.jpg', { type: 'image/jpeg' });
          await store.saveDraftPhotos(
            database, session.user.id, 'version-race', [newer], 2
          );
          let rejectedName = null;
          try {
            await store.saveDraftPhotos(
              database, session.user.id, 'version-race', [older], 1
            );
          } catch (error) {
            rejectedName = error.name;
          }
          const current = await store.loadDraftPhotos(
            database, session.user.id, 'version-race', 2
          );
          const stale = await store.loadDraftPhotos(
            database, session.user.id, 'version-race', 1
          );
          return {
            rejectedName,
            currentNames: current.map((file) => file.name),
            staleCount: stale.length,
          };
        }"""
    )

    assert result == {
        "rejectedName": "StaleDraftPhotoWriteError",
        "currentNames": ["newer.jpg"],
        "staleCount": 0,
    }


@pytest.mark.browser
def test_generation_reserves_capacity_across_tabs(page: Page, live_server_url: str) -> None:
    photo = jpeg_bytes()
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator("#account-email")).to_have_text("owner@example.com")
    seed_browser_photo_usage(page, 1024 * 1024 * 1024 - len(photo) - 1)

    second_page = page.context.new_page()
    second_page.goto(live_server_url)
    expect(second_page.locator("#account-email")).to_have_text("owner@example.com")
    generation_requests: list[str] = []
    page.context.on(
        "request",
        lambda request: (
            generation_requests.append(request.url)
            if request.url.endswith("/api/listings/generate")
            else None
        ),
    )
    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (resource, options) => {
            if (resource !== '/api/listings/generate') return originalFetch(resource, options);
            return new Promise((resolve, reject) => {
              window.releaseReservedGeneration = () => {
                originalFetch(resource, options).then(resolve, reject);
              };
            });
          };
        }"""
    )
    page.locator("#photo-input").set_input_files(
        {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": photo}
    )
    second_page.locator("#photo-input").set_input_files(
        {"name": "second.jpg", "mimeType": "image/jpeg", "buffer": photo}
    )

    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    page.wait_for_function("typeof window.releaseReservedGeneration === 'function'")
    select_generation_category(second_page)
    second_page.get_by_role("button", name="Generate listing").click()
    expect(
        second_page.get_by_text("would exceed the 1 GB draft photo limit", exact=False)
    ).to_be_visible()
    assert generation_requests == []

    page.evaluate("window.releaseReservedGeneration()")
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    expect(second_page.get_by_role("link", name="Drafts (0)")).to_be_visible()
    assert len(generation_requests) == 1
    second_page.close()


@pytest.mark.browser
def test_generation_locks_before_browser_storage_checks(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator("#account-email")).to_have_text("owner@example.com")
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    generation_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            generation_requests.append(request.url)
            if request.url.endswith("/api/listings/generate")
            else None
        ),
    )
    select_generation_category(page)
    page.evaluate(
        """() => {
          Object.defineProperty(navigator.storage, 'estimate', {
            configurable: true,
            value: () => new Promise((resolve) => {
              window.releaseStorageCheck = () => {
                resolve({ quota: 2 * 1024 ** 3, usage: 1024 });
              };
            }),
          });
          const form = document.querySelector('#listing-form');
          form.requestSubmit();
          form.requestSubmit();
        }"""
    )
    page.wait_for_function("typeof window.releaseStorageCheck === 'function'")
    expect(page.get_by_role("button", name="Looking over your photos…")).to_be_disabled()
    assert generation_requests == []

    page.evaluate("window.releaseStorageCheck()")
    expect(page.get_by_role("link", name="Drafts (1)")).to_be_visible()
    assert len(generation_requests) == 1


@pytest.mark.browser
def test_browser_photo_limit_blocks_generation(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator("#browser-storage-total")).to_contain_text("of 1 GB")

    seed_browser_photo_usage(page, int(0.76 * 1024 * 1024 * 1024))
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    expect(page.get_by_text("using more than 75%", exact=False)).to_be_visible()

    seed_browser_photo_usage(page, 900 * 1024 * 1024)
    page.locator("#photo-input").set_input_files(
        {"name": "second.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    expect(page.get_by_text("is almost full", exact=False)).to_be_visible()

    seed_browser_photo_usage(page, 1024 * 1024 * 1024)
    page.locator("#photo-input").set_input_files(
        {"name": "third.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    expect(page.get_by_text("has reached its 1 GB limit", exact=False)).to_be_visible()
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.get_by_text("would exceed the 1 GB draft photo limit", exact=False)).to_be_visible()
    expect(page.get_by_role("link", name="Drafts (0)")).to_be_visible()


@pytest.mark.browser
def test_browser_photo_limit_blocks_saved_draft_additions(page: Page, live_server_url: str) -> None:
    first_photo = jpeg_bytes()
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "first.jpg", "mimeType": "image/jpeg", "buffer": first_photo}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")

    seed_browser_photo_usage(page, 1024 * 1024 * 1024 - len(first_photo))
    page.locator("#photo-input").set_input_files(
        {"name": "blocked.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )

    expect(page.get_by_text("would exceed the 1 GB draft photo limit", exact=False)).to_be_visible()
    expect(page.locator("#photo-count")).to_have_text("1 / 24")
    expect(page.locator("#saved-drafts-list")).to_contain_text("1 photo")


@pytest.mark.browser
def test_non_json_category_error_has_safe_message(page: Page, live_server_url: str) -> None:
    page.route(
        "**/api/ebay/categories/*/requirements",
        lambda route: route.fulfill(status=500, content_type="text/plain", body="Internal error"),
    )
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing").click()

    expect(page.get_by_text("eBay condition details failed.", exact=True)).to_be_visible()
    expect(page.get_by_label("Item specific 1 name")).not_to_have_attribute("readonly", "")
    expect(page.get_by_label("Item specific 1 value")).to_have_attribute("data-copy-label", "Brand")
    page.get_by_label("Item specific 1 name").fill("Maker")
    expect(page.get_by_label("Item specific 1 value")).to_have_attribute("data-copy-label", "Maker")
    page.get_by_role("button", name="Add custom item specific").click()
    page.get_by_label("Item specific 3 name").fill("Seller field")
    page.get_by_label("Item specific 3 value", exact=True).fill("Seller value")
    page.locator(".specific-row").nth(2).get_by_role("button", name="Add another value").click()
    page.get_by_label("Item specific 3 value 2", exact=True).fill("Second value")
    expect(page.get_by_text("The string did not match the expected pattern.")).to_have_count(0)


def open_new_category_flow(page: Page, live_server_url: str) -> None:
    page.goto(f"{live_server_url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    page.locator("#photo-input").set_input_files(
        {"name": "sweater.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )


@pytest.mark.browser
@pytest.mark.parametrize("connected", [True, False])
def test_explicit_category_selection_and_generation_request(
    page: Page, live_server_url: str, connected: bool
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    if not connected:

        def disconnected(route):
            response = route.fetch()
            payload = response.json()
            payload["connected"] = False
            payload["direct_publish_enabled"] = False
            route.fulfill(response=response, json=payload)

        page.route("**/api/ebay/connection", disconnected)
    searches = []
    page.on(
        "request",
        lambda request: (
            searches.append(request.url)
            if "/category-groups/" in request.url and request.url.endswith("/categories")
            else None
        ),
    )
    open_new_category_flow(page, live_server_url)
    generate = page.get_by_role("button", name="Generate listing", exact=True)
    expect(generate).to_be_disabled()
    expect(page.locator("#generation-readiness")).to_have_text("Select an eBay category.")
    page.get_by_label("Category group", exact=True).select_option("260010")
    expect(page.locator("#generation-category-status")).to_have_text(
        "Select the category that matches your item."
    )
    expect(page.get_by_label("Category for generation", exact=True)).to_have_value("")
    expect(generate).to_be_disabled()
    page.get_by_label("Category for generation", exact=True).select_option("175786")
    expect(generate).to_be_enabled()
    with page.expect_response("**/api/listings/generate") as generated:
        generate.click()
    assert generated.value.json()["ebay_category"]["category_id"] == "175786"
    expect(page.get_by_label("Title", exact=True)).to_have_value(StubGenerator().draft.title)
    expect(generate).to_be_enabled()
    expect(page.get_by_label("eBay category", exact=True)).to_have_value("175786")
    expect(page.get_by_label("Category for generation", exact=True)).to_have_value("175786")
    assert len(searches) >= 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # A copy-only edit must retain the server-saved category too.
    page.get_by_label("Title", exact=True).fill("Category retained")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")


@pytest.mark.browser
def test_category_retry_preserves_inputs_and_freezes_category_controls(
    page: Page, live_server_url: str
) -> None:
    open_new_category_flow(page, live_server_url)
    select_generation_category(page)
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        if (args[0] === '/api/listings/generate' && !window.categoryFailureUsed) {
          window.categoryFailureUsed = true;
          return await new Promise(resolve => {
            window.finishCategoryFailure = () => resolve(new Response(
            JSON.stringify({detail: 'Could not load the selected category. Retry.'}),
            {status: 502, headers: {'Content-Type': 'application/json'}}
          )); });
        }
        return original(...args);
      };
    }""")
    page.get_by_role("button", name="Generate listing", exact=True).click()
    page.wait_for_function("Boolean(window.finishCategoryFailure)")
    expect(page.get_by_label("Category for generation", exact=True)).to_be_disabled()
    expect(page.get_by_label("Category group", exact=True)).to_be_disabled()
    page.evaluate("window.finishCategoryFailure()")
    expect(page.locator("#error-message")).to_contain_text("Could not load the selected category")
    expect(page.get_by_label("Category for generation", exact=True)).to_have_value("175786")
    expect(page.locator("#photo-count")).to_have_text("1 / 24")
    page.get_by_role("button", name="Generate listing", exact=True).click()
    expect(page.get_by_label("Title", exact=True)).to_have_value(StubGenerator().draft.title)


@pytest.mark.browser
def test_old_group_response_cannot_replace_current_group(page: Page, live_server_url: str) -> None:
    open_new_category_flow(page, live_server_url)
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        if (String(args[0]).includes('/category-groups/260012/categories')) {
          return await new Promise(resolve => { window.finishOldGroup = () => resolve(new Response(
            JSON.stringify({group_id: '260012', categories: [
              {category_id: '999', name: 'Old', path: 'Men > Old'}
            ]}),
            {status: 200, headers: {'Content-Type': 'application/json'}}
          )); });
        }
        return original(...args);
      };
    }""")
    page.get_by_label("Category group", exact=True).select_option("260012")
    page.wait_for_function("Boolean(window.finishOldGroup)")
    select_generation_category(page)
    page.evaluate("window.finishOldGroup()")
    expect(page.get_by_label("Category for generation", exact=True)).to_have_value("175786")
    expect(page.locator('#generation-category option[value="999"]')).to_have_count(0)
    page.get_by_role("button", name="Generate listing", exact=True).click()
    expect(page.get_by_label("Title", exact=True)).to_have_value(StubGenerator().draft.title)


@pytest.mark.browser
def test_menu_selection_is_saved_with_open_draft(page: Page, live_server_url: str) -> None:
    open_new_category_flow(page, live_server_url)
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing", exact=True).click()
    expect(page.get_by_label("Title", exact=True)).to_have_value(StubGenerator().draft.title)
    expect(page.get_by_role("button", name="Generate listing", exact=True)).to_be_enabled()
    page.route(
        "**/api/ebay/category-groups/260012/categories",
        lambda route: route.fulfill(
            json={
                "group_id": "260012",
                "categories": [
                    {"category_id": "123", "name": "Shoes", "path": "Men > Shoes"},
                ],
            }
        ),
    )
    page.get_by_label("Category group", exact=True).select_option("260012")
    page.get_by_label("Category for generation", exact=True).select_option("123")
    expect(page.get_by_label("eBay category", exact=True)).to_have_value("123")
    page.get_by_label("Title", exact=True).fill("Original sweater")
    expect(page.locator("#draft-save-status")).to_have_text("Saving…")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.get_by_role("button", name="Generate listing", exact=True).click()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    expect(page.get_by_label("eBay category", exact=True)).to_have_value("123")
    row = page.locator(".saved-draft-row").filter(has_text="Original sweater")
    row.get_by_role("button", name="Open", exact=True).click()
    expect(page.get_by_label("Category for generation", exact=True)).to_have_value("123")
    expect(page.get_by_label("eBay category", exact=True)).to_have_value("123")
    expect(page.get_by_label("Category group", exact=True)).to_have_value("260012")
    expect(page.get_by_role("button", name="Generate listing", exact=True)).to_be_enabled()


@pytest.mark.browser
def test_publication_recovery_does_not_block_a_separate_generation(
    page: Page, live_server_url: str
) -> None:
    open_new_category_flow(page, live_server_url)
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing", exact=True).click()
    expect(page.get_by_label("Title", exact=True)).to_have_value(StubGenerator().draft.title)
    page.get_by_label("Ounces", exact=True).fill("8")
    page.get_by_label("Length", exact=True).fill("12")
    page.get_by_label("Width", exact=True).fill("8")
    page.get_by_label("Height", exact=True).fill("3")
    page.route(
        "**/api/ebay/listings/*/publish",
        lambda route: route.fulfill(
            status=503,
            json={"code": "ebay_publish_uncertain", "detail": "Retry the protected publication."},
        ),
    )
    page.get_by_role("button", name="Publish live on eBay", exact=True).click()
    expect(page.get_by_role("button", name="Retry publish", exact=True)).to_be_visible()
    expect(page.get_by_label("Category for generation", exact=True)).to_be_enabled()
    page.get_by_role("button", name="Generate listing", exact=True).click()
    expect(page.get_by_role("link", name="Drafts (2)")).to_be_visible()
    expect(page.get_by_role("button", name="Publish live on eBay", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Generate listing", exact=True)).to_be_enabled()
    expect(page.get_by_label("eBay category", exact=True)).to_be_enabled()
    expect(page.get_by_label("Shipping policy", exact=True)).to_be_enabled()


@pytest.mark.browser
@pytest.mark.parametrize("width", [390, 1280])
def test_group_menus_cover_inventory_and_clear_selection(
    page: Page, live_server_url: str, width: int
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    open_new_category_flow(page, live_server_url)
    group = page.get_by_label("Category group", exact=True)
    category = page.get_by_label("Category for generation", exact=True)
    expect(group).to_be_visible()
    expect(category).to_be_visible()
    assert group.bounding_box()["y"] < page.locator("#generate-button").bounding_box()["y"]
    assert category.bounding_box()["y"] < page.locator("#generate-button").bounding_box()["y"]
    if width > 480:
        assert group.bounding_box()["y"] == category.bounding_box()["y"]
    for group_id, category_id in [
        ("260010", "175786"),
        ("260012", "2600121"),
        ("171146", "1711461"),
    ]:
        group.select_option(group_id)
        expect(page.locator("#generation-category-status")).to_have_text(
            "Select the category that matches your item."
        )
        expect(category).to_have_value("")
        expect(page.locator("#generate-button")).to_be_disabled()
        assert category.locator("option").evaluate_all(
            "(options) => options.map(option => option.value)"
        ) == ["", category_id]
        category.select_option(category_id)
        expect(page.locator("#generate-button")).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
def test_group_menu_failure_can_retry_without_search(page: Page, live_server_url: str) -> None:
    open_new_category_flow(page, live_server_url)
    url = "**/api/ebay/category-groups/260010/categories"
    page.route(
        url,
        lambda route: route.fulfill(status=502, json={"detail": "Category loading failed. Retry."}),
    )
    page.get_by_label("Category group", exact=True).select_option("260010")
    expect(page.get_by_role("button", name="Retry categories")).to_be_visible()
    expect(page.locator("#generate-button")).to_be_disabled()
    page.unroute(url)
    page.get_by_role("button", name="Retry categories").click()
    page.get_by_label("Category for generation", exact=True).select_option("175786")
    expect(page.locator("#generate-button")).to_be_enabled()


@pytest.mark.browser
def test_saved_groups_restore_per_draft_and_ignore_late_previous_load(
    page: Page, live_server_url: str
) -> None:
    open_new_category_flow(page, live_server_url)
    draft_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    categories = [
        GENERATION_CATEGORY,
        {"group_id": "260012", "category_id": "2600121", "name": "Shirts", "path": "Men > Shirts"},
    ]
    for index, category in enumerate(categories):
        draft = StubGenerator().draft.model_dump(mode="json")
        draft["title"] = f"Saved group {index}"
        assert (
            page.evaluate(
                """async ({id, draft, category}) => {
          const response = await fetch(`/api/drafts/${id}`, {method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({revision: null, draft, ebay_category: category,
              ebay_condition: {condition_id: 3000, name: 'Pre-owned'}, photo_count: 1})});
          return response.status;
        }""",
                {"id": draft_ids[index], "draft": draft, "category": category},
            )
            == 200
        )
    page.reload()
    generated = []
    page.on(
        "request",
        lambda request: (
            generated.append(request.url)
            if request.url.endswith("/api/listings/generate")
            else None
        ),
    )
    for index in [0, 1, 0]:
        page.locator(".saved-draft-row").filter(has_text=f"Saved group {index}").get_by_role(
            "button", name="Open", exact=True
        ).click()
        expect(page.get_by_label("Category group", exact=True)).to_have_value(
            categories[index]["group_id"]
        )
        expect(page.get_by_label("Category for generation", exact=True)).to_have_value(
            categories[index]["category_id"]
        )
        expect(page.locator("#generation-category-status")).to_contain_text("Selected category:")
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        if (String(args[0]).endsWith('/category-groups/260010/categories')) {
          const response = await original(...args);
          return await new Promise(resolve => {
            window.finishPriorDraftGroup = () => resolve(response);
          });
        }
        return original(...args);
      };
    }""")
    page.locator(".saved-draft-row").filter(has_text="Saved group 0").get_by_role(
        "button", name="Open", exact=True
    ).click()
    page.wait_for_function("Boolean(window.finishPriorDraftGroup)")
    page.locator(".saved-draft-row").filter(has_text="Saved group 1").get_by_role(
        "button", name="Open", exact=True
    ).click()
    expect(page.get_by_label("Category group", exact=True)).to_have_value("260012")
    page.evaluate("window.finishPriorDraftGroup()")
    expect(page.get_by_label("Category for generation", exact=True)).to_have_value("2600121")
    expect(page.locator('#generation-category option[value="175786"]')).to_have_count(0)
    assert generated == []


@pytest.mark.browser
def test_photo_selected_before_app_module_load_is_retained(
    page: Page, live_server_url: str
) -> None:
    open_new_category_flow(page, live_server_url)
    pending = []
    page.route("**/static/app.js*", lambda route: pending.append(route))
    page.goto(live_server_url, wait_until="commit")
    page.locator("#photo-input").set_input_files(
        {"name": "early.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()}
    )
    assert pending
    pending[0].fulfill(response=pending[0].fetch())
    expect(page.get_by_label("Category group", exact=True).locator("option")).to_have_count(4)
    expect(page.locator("#photo-count")).to_have_text("1 / 24")


@pytest.mark.browser
def test_category_change_requires_explicit_generation_after_reopening(
    page: Page, live_server_url: str
) -> None:
    open_new_category_flow(page, live_server_url)
    generations = []
    page.on(
        "request",
        lambda request: (
            generations.append(request.url)
            if request.url.endswith("/api/listings/generate")
            else None
        ),
    )
    select_generation_category(page)
    with page.expect_response("**/api/listings/generate") as first:
        page.get_by_role("button", name="Generate listing", exact=True).click()
    original_id = first.value.json()["draft_id"]
    expect(page.get_by_label("Title", exact=True)).to_be_visible()
    page.get_by_label("Title", exact=True).fill("Draft before category change")
    page.get_by_label("Ounces", exact=True).fill("8")
    page.get_by_label("Length", exact=True).fill("12")
    page.get_by_label("Width", exact=True).fill("8")
    page.get_by_label("Height", exact=True).fill("3")
    expect(page.get_by_role("button", name="Publish live on eBay", exact=True)).to_be_enabled()
    page.get_by_label("Category group", exact=True).select_option("260012")
    page.get_by_label("Category for generation", exact=True).select_option("2600121")
    expect(page.locator("#category-regeneration-message")).to_contain_text(
        "Select Generate listing again"
    )
    expect(page.get_by_role("button", name="Publish live on eBay", exact=True)).to_be_disabled()
    expect(page.get_by_label("Title", exact=True)).to_have_value("Draft before category change")
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    assert len(generations) == 1
    page.locator(".saved-draft-row").filter(has_text="Draft before category change").get_by_role(
        "button", name="Open", exact=True
    ).click()
    expect(page.get_by_label("Category group", exact=True)).to_have_value("260012")
    expect(page.get_by_label("Category for generation", exact=True)).to_have_value("2600121")
    expect(page.locator("#category-regeneration-message")).to_be_visible()
    expect(page.get_by_role("button", name="Publish live on eBay", exact=True)).to_be_disabled()
    assert len(generations) == 1
    url = "**/api/listings/generate"
    page.route(
        url, lambda route: route.fulfill(status=502, json={"detail": "Generation failed. Retry."})
    )
    page.get_by_role("button", name="Generate listing", exact=True).click()
    expect(page.locator("#error-message")).to_contain_text("Generation failed")
    expect(page.locator("#category-regeneration-message")).to_be_visible()
    expect(page.get_by_label("Category for generation", exact=True)).to_have_value("2600121")
    page.unroute(url)
    with page.expect_response(url) as retried:
        page.get_by_role("button", name="Generate listing", exact=True).click()
    payload = retried.value.json()
    assert payload["ebay_category"]["category_id"] == "2600121"
    assert payload["ebay_category"]["group_id"] == "260012"
    assert not payload["draft"]["generation_required"]
    assert [(item["name"], item["values"]) for item in payload["draft"]["item_specifics"]] == [
        ("Brand", ["Everlane"]),
        ("Size", ["M"]),
    ]
    expect(page.locator("#category-regeneration-message")).to_be_hidden()
    assert len(generations) == 3
    assert page.evaluate(
        "id => fetch(`/api/drafts/${id}`).then(r => r.json())"
        ".then(d => d.draft.generation_required)",
        original_id,
    )


@pytest.mark.browser
def test_returning_to_original_category_does_not_clear_regeneration(
    page: Page, live_server_url: str
) -> None:
    open_new_category_flow(page, live_server_url)
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing", exact=True).click()
    expect(page.get_by_label("Title", exact=True)).to_be_visible()
    page.get_by_label("Category group", exact=True).select_option("260012")
    page.get_by_label("Category for generation", exact=True).select_option("2600121")
    page.get_by_label("Category group", exact=True).select_option("260010")
    page.get_by_label("Category for generation", exact=True).select_option("175786")
    expect(page.locator("#category-regeneration-message")).to_be_visible()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.locator(".saved-draft-row").get_by_role("button", name="Open", exact=True).click()
    expect(page.locator("#category-regeneration-message")).to_be_visible()
    expect(page.get_by_label("Category for generation", exact=True)).to_have_value("175786")


@pytest.mark.browser
def test_late_autosave_cannot_clear_new_category_invalidation(
    page: Page, live_server_url: str
) -> None:
    open_new_category_flow(page, live_server_url)
    select_generation_category(page)
    page.get_by_role("button", name="Generate listing", exact=True).click()
    expect(page.get_by_label("Title", exact=True)).to_be_visible()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (String(args[0]).startsWith('/api/drafts/') && args[1]?.method === 'PUT'
            && !window.heldCategorySave) {
          window.heldCategorySave = true;
          return await new Promise(resolve => {
            window.finishCategorySave = () => resolve(response);
          });
        }
        return response;
      };
    }""")
    page.get_by_label("Title", exact=True).fill("Held category save")
    page.wait_for_function("Boolean(window.finishCategorySave)")
    page.get_by_label("Category group", exact=True).select_option("260012")
    page.get_by_label("Category for generation", exact=True).select_option("2600121")
    page.evaluate("window.finishCategorySave()")
    expect(page.locator("#category-regeneration-message")).to_be_visible()
    expect(page.locator("#draft-save-status")).to_have_text("Saved")
    page.locator(".saved-draft-row").filter(has_text="Held category save").get_by_role(
        "button", name="Open", exact=True
    ).click()
    expect(page.get_by_label("Category for generation", exact=True)).to_have_value("2600121")
    expect(page.locator("#category-regeneration-message")).to_be_visible()


@pytest.fixture
def cached_release_url(browser_application):
    state = {"old_release": True, "old_script_requests": 0}

    @browser_application.middleware("http")
    async def prior_release(request, call_next):
        if request.url.path == "/cache-primer":
            return HTMLResponse(
                '<input id="generation-category-query">'
                '<script type="module" src="/static/app.js?v=20260902-1"></script>',
                headers={"Cache-Control": "no-store"},
            )
        if (
            state["old_release"]
            and request.url.path == "/static/app.js"
            and request.url.query == "v=20260902-1"
        ):
            state["old_script_requests"] += 1
            # The prior release used this removed control during module startup.
            return Response(
                "window.oldCategoryScript = true;"
                'document.querySelector("#generation-category-query")'
                '.addEventListener("input", () => {});',
                media_type="text/javascript",
                headers={"Cache-Control": "private, max-age=86400"},
            )
        return await call_next(request)

    with running_browser_server(browser_application) as url:
        yield url, state


@pytest.mark.browser
def test_category_groups_load_with_previous_release_in_browser_cache(
    page: Page, cached_release_url
) -> None:
    url, state = cached_release_url
    page.goto(f"{url}/cache-primer")
    assert page.evaluate("window.oldCategoryScript") is True
    state["old_release"] = False
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # Do not use page.route: it disables the real browser HTTP cache.
    page.goto(f"{url}/login")
    page.get_by_label("Email address", exact=True).fill("owner@example.com")
    page.get_by_label("Password").fill("correct-horse-battery-staple")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator("#generation-category-group option")).to_have_text(
        ["Select a group", "Men", "Women", "Kids"]
    )
    page.get_by_label("Category group", exact=True).select_option("260010")
    expect(page.locator("#generation-category option")).to_have_count(2)
    assert state["old_script_requests"] == 1
    assert page.evaluate("window.oldCategoryScript") is None
    assert errors == []
