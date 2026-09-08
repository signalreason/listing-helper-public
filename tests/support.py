from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image

from app.accounts import MemoryAccountStore
from app.auth import Passwords
from app.ebay_api import EbayApiError, EbayIdentity, OAuthGrant
from app.ebay_models import (
    CategoryRequirements,
    EbayAspect,
    EbayAspectValue,
    EbayAspectValueConstraint,
    EbayCategory,
    EbayCondition,
    FeedTask,
    MediaReference,
)
from app.ebay_security import TokenCipher
from app.ebay_store import MemoryEbayStore
from app.main import create_app
from app.models import ItemSpecific, ListingDraft, PricingGuidance


def jpeg_bytes(width: int = 800, height: int = 800, color: str = "#3e6f54") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def listing_draft() -> ListingDraft:
    return ListingDraft(
        title="Everlane Green Cotton Crewneck Sweater Women's Medium",
        suggested_category="Women's Clothing > Sweaters",
        search_terms=["Everlane", "green sweater", "women's medium", "cotton crewneck"],
        condition="Pre-owned",
        condition_description="Good pre-owned condition with no visible holes or stains.",
        description=(
            "Green Everlane crewneck sweater in a women's medium. Review measurements and "
            "fabric content before listing."
        ),
        item_specifics=[
            ItemSpecific(name="Brand", value="Everlane", source="photo", confidence="high"),
            ItemSpecific(name="Size", value="M", source="photo", confidence="medium"),
        ],
        quantity=1,
        recommended_listing_format="fixed_price",
        observed_flaws=[],
        missing_facts=["Confirm measurements", "Confirm fabric content"],
        pricing=PricingGuidance(
            suggested_price=32.0,
            expected_sale_price_low=24.0,
            expected_sale_price_high=30.0,
            currency="USD",
            confidence="medium",
            rationale="Estimate based on the visible brand, style, and apparent condition.",
        ),
    )


class StubGenerator:
    def __init__(self, draft: ListingDraft | None = None) -> None:
        self.draft = draft or listing_draft()
        self.calls: list[tuple[int, str]] = []
        self.paths = []

    def generate(self, images, notes: str, category, requirements) -> ListingDraft:
        self.category = category
        self.requirements = requirements
        assert all(image.path.exists() for image in images)
        assert all(image.mime_type == "image/jpeg" for image in images)
        self.paths.extend(image.path for image in images)
        self.calls.append((len(images), notes))
        return self.draft


class StubEbayGateway:
    environment = "sandbox"

    def __init__(self) -> None:
        self.uploads: list[bytes] = []
        self.upload_paths = []
        self.feed_calls = []
        self.feed_status = "QUEUED"
        self.feed_result = "Action,Status,ErrorMessage\nDraft,Failed,Choose a required size"
        self.feed_input: bytes | None = None
        self.fail_upload_number: int | None = None
        self.fail_feed_upload = False
        self.task_creations = 0

    def configured(self) -> bool:
        return True

    def authorization_url(self, state: str) -> str:
        return f"https://auth.sandbox.ebay.com/oauth?state={state}"

    def exchange_code(self, code: str) -> OAuthGrant:
        assert code
        return OAuthGrant(refresh_token="refresh-secret", access_token="access-token")

    def identity(self, access_token: str) -> EbayIdentity:
        assert access_token == "access-token"
        return EbayIdentity(user_id="immutable-ebay-user", display_name="seller-name")

    def user_access_token(self, refresh_token: str) -> str:
        if refresh_token == "expired":
            raise EbayApiError("expired", authorization_expired=True)
        assert refresh_token == "refresh-secret"
        return "access-token"

    def category_suggestions(self, query: str) -> list[EbayCategory]:
        assert query
        return [
            EbayCategory(
                category_id="175786",
                name="Sweaters",
                path="Clothing, Shoes & Accessories > Women > Sweaters",
            )
        ]

    def category_subtree(self, group_id: str) -> list[EbayCategory]:
        if group_id == "260010":
            return self.category_suggestions("women")
        name = "Men" if group_id == "260012" else "Kids"
        return [EbayCategory(category_id=group_id + "1", name="Shirts", path=f"{name} > Shirts")]

    def category_requirements(self, category_id: str) -> CategoryRequirements:
        return CategoryRequirements(
            category=EbayCategory(category_id=category_id, name="Sweaters", path="Sweaters"),
            conditions=[EbayCondition(condition_id=3000, name="Pre-owned")],
            aspects=[
                EbayAspect(name="Brand", required=True, applicable_to=["product"]),
                EbayAspect(
                    name="Size",
                    values=["S", "M", "L"],
                    required=True,
                    mode="selection",
                    applicable_to=["item"],
                ),
                EbayAspect(name="Color", recommended=True, applicable_to=["product"]),
                EbayAspect(
                    name="Material",
                    values=["Cotton", "Wool"],
                    mode="selection",
                    cardinality="multi",
                    applicable_to=["item", "product"],
                ),
                EbayAspect(name="Metal", values=["Gold", "Silver"], mode="selection"),
                EbayAspect(
                    name="Metal Purity",
                    values=[
                        EbayAspectValue(
                            value="10k",
                            constraints=[
                                EbayAspectValueConstraint(aspect_name="Metal", values=["Gold"])
                            ],
                        )
                    ],
                    mode="selection",
                ),
                EbayAspect(name="Short note", max_length=5),
            ],
        )

    def upload_image(self, access_token: str, path) -> MediaReference:
        assert access_token == "access-token"
        data = path.read_bytes()
        self.upload_paths.append(path)
        number = len(self.uploads) + 1
        if self.fail_upload_number == number:
            raise EbayApiError("upload failed")
        self.uploads.append(data)
        return MediaReference(
            image_id=f"image-{number}",
            image_url=f"https://i.ebayimg.test/image-{number}.jpg",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )

    def create_draft_feed_task(self, access_token):
        assert access_token == "access-token"
        self.task_creations += 1
        return FeedTask(task_id="feed-task-1", status="CREATED")

    def upload_draft_feed(self, access_token, task_id, listing, custom_label, image_urls):
        self.feed_calls.append((access_token, listing, custom_label, image_urls))
        if self.fail_feed_upload:
            raise EbayApiError("upload confirmation timed out")
        return FeedTask(task_id="feed-task-1", status="QUEUED")

    def get_feed_task(self, access_token: str, task_id: str) -> FeedTask:
        assert access_token == "access-token"
        return FeedTask(task_id=task_id, status=self.feed_status)

    def get_feed_result(self, access_token: str, task_id: str) -> str:
        return self.feed_result

    def get_feed_input(self, access_token: str, task_id: str) -> bytes:
        if self.feed_input is None:
            raise EbayApiError("no Feed input fixture")
        return self.feed_input

    def business_policies(self, access_token: str):
        assert access_token == "access-token"
        return [
            {"type": "fulfillment_policy", "id": "shipping", "name": "Standard shipping"},
            {"type": "payment_policy", "id": "payment", "name": "Managed payments"},
            {"type": "return_policy", "id": "returns", "name": "30 day returns"},
        ]


class StubNotificationVerifier:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.calls = []

    def verify(self, body: bytes, signature: str) -> bool:
        self.calls.append((body, signature))
        return self.valid


def authenticated_client(
    generator=None,
    *,
    owner: bool = True,
    ebay_gateway=None,
    ebay_store=None,
    notification_verifier=None,
    trading_adapter=None,
):
    store = MemoryAccountStore()
    password = "correct-horse-battery-staple"
    password_hash = Passwords().hash(password)
    if owner:
        user = store.create_owner("owner@example.com", password_hash)
    else:
        store.create_owner("owner@example.com", password_hash)
        user = store.create_user("user@example.com", password_hash)
    application = create_app(
        generator or StubGenerator(),
        store,
        ebay_gateway or StubEbayGateway(),
        ebay_store or MemoryEbayStore(),
        session_secret="test-session-secret-that-is-long-enough",
        cookie_secure=False,
        ebay_token_cipher=TokenCipher(Fernet.generate_key().decode()),
        ebay_notification_verifier=notification_verifier or StubNotificationVerifier(),
        trading_adapter=trading_adapter,
    )
    client = TestClient(application)
    response = client.post(
        "/api/sessions",
        json={"email": user.email, "password": password},
    )
    assert response.status_code == 200
    return client, store, user


GENERATION_CATEGORY = {
    "group_id": "260010",
    "category_id": "175786",
    "name": "Sweaters",
    "path": "Women > Sweaters",
}
