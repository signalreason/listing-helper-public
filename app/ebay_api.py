"""Modern eBay support APIs and Seller Hub Feed transport."""

from __future__ import annotations

import base64
import csv
import gzip
import io
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from app.ebay_models import (
    CategoryRequirements,
    EbayAspect,
    EbayAspectValue,
    EbayAspectValueConstraint,
    EbayCategory,
    EbayCondition,
    EbayReadyListing,
    FeedTask,
    MediaReference,
)

OAUTH_SCOPES = (
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account.readonly",
)

DRAFT_FEED_TEMPLATE = "eBay-draft-listings-template_US"
DRAFT_FEED_TEMPLATE_VERSION = "0.0.2"
DRAFT_FEED_ACTION_HEADER = "Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)"
DRAFT_FEED_FILENAME_PREFIX = "eBay-draft-listing-template"
APPLICATION_TOKEN_DEFAULT_LIFETIME_SECONDS = 7_200
APPLICATION_TOKEN_REFRESH_MARGIN_SECONDS = 60
CATEGORY_LOGGER = logging.getLogger("app.ebay.category")


def _taxonomy_enum(
    value: object,
    *,
    mapping: dict[str, str] | None = None,
    default: str | None = None,
) -> str:
    raw = value if isinstance(value, str) and value else default
    if raw is None:
        return ""
    return (mapping or {}).get(raw, raw.lower())


def _taxonomy_enums(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [_taxonomy_enum(item) for item in values if isinstance(item, str) and item]


def draft_feed_filename(created_at: datetime | None = None) -> str:
    timestamp = created_at or datetime.now(UTC)
    return f"{DRAFT_FEED_FILENAME_PREFIX}-{timestamp:%b-%d-%Y-%H-%M-%S}.csv"


class EbayApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        authorization_expired: bool = False,
        failure_class: str = "provider",
        provider_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.authorization_expired = authorization_expired
        self.failure_class = failure_class
        self.provider_status = provider_status


@dataclass(frozen=True)
class OAuthGrant:
    refresh_token: str
    access_token: str


@dataclass(frozen=True)
class EbayIdentity:
    user_id: str
    display_name: str


@dataclass(frozen=True)
class _CachedApplicationToken:
    value: str
    refresh_at: float


class EbayGateway(Protocol):
    environment: str

    def configured(self) -> bool: ...

    def authorization_url(self, state: str) -> str: ...

    def exchange_code(self, code: str) -> OAuthGrant: ...

    def identity(self, access_token: str) -> EbayIdentity: ...

    def user_access_token(self, refresh_token: str) -> str: ...

    def category_suggestions(self, query: str) -> list[EbayCategory]: ...

    def category_subtree(self, group_id: str) -> list[EbayCategory]: ...

    def category_requirements(self, category_id: str) -> CategoryRequirements: ...

    def upload_image(self, access_token: str, path: Path) -> MediaReference: ...

    def create_draft_feed_task(self, access_token: str) -> FeedTask: ...

    def upload_draft_feed(
        self,
        access_token: str,
        task_id: str,
        listing: EbayReadyListing,
        custom_label: str,
        image_urls: list[str],
    ) -> FeedTask: ...

    def get_feed_task(self, access_token: str, task_id: str) -> FeedTask: ...

    def get_feed_result(self, access_token: str, task_id: str) -> str: ...

    def get_feed_input(self, access_token: str, task_id: str) -> bytes: ...

    def business_policies(self, access_token: str) -> list[dict[str, str]]: ...


class EbayHttpGateway:
    """Small synchronous client; FastAPI runs these calls in a thread pool."""

    def __init__(
        self,
        *,
        environment: str | None = None,
        timeout: float = 30,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.environment = environment or os.environ.get("EBAY_ENVIRONMENT", "sandbox")
        self.timeout = timeout
        self._clock = clock
        self._application_token: _CachedApplicationToken | None = None
        self._application_token_lock = threading.Lock()

    @property
    def rest_host(self) -> str:
        return "api.sandbox.ebay.com" if self.environment == "sandbox" else "api.ebay.com"

    @property
    def media_host(self) -> str:
        return "apim.sandbox.ebay.com" if self.environment == "sandbox" else "apim.ebay.com"

    def _credentials(self) -> tuple[str, str, str]:
        values = (
            os.environ.get("EBAY_CLIENT_ID"),
            os.environ.get("EBAY_CLIENT_SECRET"),
            os.environ.get("EBAY_RUNAME"),
        )
        if not all(values):
            raise EbayApiError("eBay is not configured")
        return values[0], values[1], values[2]

    def configured(self) -> bool:
        return all(
            os.environ.get(name) for name in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_RUNAME")
        )

    def authorization_url(self, state: str) -> str:
        client_id, _, runame = self._credentials()
        host = "auth.sandbox.ebay.com" if self.environment == "sandbox" else "auth.ebay.com"
        query = urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": runame,
                "scope": " ".join(OAUTH_SCOPES),
                "state": state,
                "prompt": "login",
            }
        )
        return f"https://{host}/oauth2/authorize?{query}"

    def _token(self, data: dict[str, str]) -> dict[str, object]:
        client_id, client_secret, _ = self._credentials()
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        try:
            response = httpx.post(
                f"https://{self.rest_host}/identity/v1/oauth2/token",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=data,
                timeout=self.timeout,
            )
        except httpx.HTTPError as error:
            raise EbayApiError("eBay authorization failed", failure_class="transport") from error
        if response.status_code >= 400:
            expired = response.status_code == 400 and "invalid_grant" in response.text
            raise EbayApiError(
                "eBay authorization failed",
                authorization_expired=expired,
                failure_class="authorization",
                provider_status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise EbayApiError(
                "eBay returned an invalid response",
                failure_class="invalid_response",
                provider_status=response.status_code,
            ) from error
        if not isinstance(payload, dict):
            raise EbayApiError(
                "eBay returned an invalid response",
                failure_class="invalid_response",
                provider_status=response.status_code,
            )
        return payload

    def exchange_code(self, code: str) -> OAuthGrant:
        _, _, runame = self._credentials()
        payload = self._token(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": runame}
        )
        refresh_token = payload.get("refresh_token")
        access_token = payload.get("access_token")
        if not isinstance(refresh_token, str) or not isinstance(access_token, str):
            raise EbayApiError("eBay did not return a reusable authorization")
        return OAuthGrant(refresh_token=refresh_token, access_token=access_token)

    def user_access_token(self, refresh_token: str) -> str:
        payload = self._token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": " ".join(OAUTH_SCOPES),
            }
        )
        access_token = payload.get("access_token")
        if not isinstance(access_token, str):
            raise EbayApiError("eBay did not return an access token")
        return access_token

    def _application_access_token(self) -> str:
        return self._application_access_token_state()[0]

    def _application_access_token_state(self) -> tuple[str, bool]:
        with self._application_token_lock:
            now = self._clock()
            if self._application_token and self._application_token.refresh_at > now:
                return self._application_token.value, True
            payload = self._token(
                {
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope",
                }
            )
            token = payload.get("access_token")
            if not isinstance(token, str):
                raise EbayApiError(
                    "eBay did not return an application token", failure_class="invalid_response"
                )
            expires_in = payload.get("expires_in", APPLICATION_TOKEN_DEFAULT_LIFETIME_SECONDS)
            try:
                lifetime = float(expires_in)
            except (TypeError, ValueError) as error:
                raise EbayApiError(
                    "eBay returned an invalid application token lifetime",
                    failure_class="invalid_response",
                ) from error
            if lifetime <= 0:
                raise EbayApiError(
                    "eBay returned an invalid application token lifetime",
                    failure_class="invalid_response",
                )
            refresh_after = max(lifetime - APPLICATION_TOKEN_REFRESH_MARGIN_SECONDS, 0)
            self._application_token = _CachedApplicationToken(
                value=token,
                refresh_at=now + refresh_after,
            )
            return token, False

    def _clear_application_token(self, token: str) -> None:
        with self._application_token_lock:
            if self._application_token and self._application_token.value == token:
                self._application_token = None

    def _application_get(self, operation: str, path: str, **parameters) -> dict[str, object]:
        started_at = self._clock()
        attempt_count = 1
        try:
            token, used_cached_token = self._application_access_token_state()
            try:
                payload = self._get(path, token, **parameters)
            except EbayApiError as error:
                if error.provider_status != 401 or not used_cached_token:
                    raise
                self._clear_application_token(token)
                attempt_count = 2
                refreshed_token, _ = self._application_access_token_state()
                payload = self._get(path, refreshed_token, **parameters)
        except EbayApiError as error:
            safe_error = EbayApiError(
                "eBay could not complete the request",
                failure_class=error.failure_class,
                provider_status=error.provider_status,
            )
            self._log_application_get(
                operation=operation,
                failure_class=safe_error.failure_class,
                provider_status=safe_error.provider_status,
                attempt_count=attempt_count,
                outcome="failure",
                started_at=started_at,
            )
            raise safe_error from error
        self._log_application_get(
            operation=operation,
            failure_class="none",
            provider_status=200,
            attempt_count=attempt_count,
            outcome="success",
            started_at=started_at,
        )
        return payload

    def _log_application_get(
        self,
        *,
        operation: str,
        failure_class: str,
        provider_status: int | None,
        attempt_count: int,
        outcome: str,
        started_at: float,
    ) -> None:
        CATEGORY_LOGGER.info(
            "operation=%s failure_class=%s provider_status=%s "
            "attempt_count=%d outcome=%s duration_ms=%d",
            operation,
            failure_class,
            provider_status if provider_status is not None else "none",
            attempt_count,
            outcome,
            round((self._clock() - started_at) * 1_000),
        )

    def _get(self, path: str, token: str, **parameters) -> dict[str, object]:
        try:
            response = httpx.get(
                f"https://{self.rest_host}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                },
                params=parameters,
                timeout=self.timeout,
            )
        except httpx.HTTPError as error:
            raise EbayApiError(
                "eBay could not complete the request", failure_class="transport"
            ) from error
        if response.status_code == 401:
            raise EbayApiError(
                "eBay authorization expired",
                authorization_expired=True,
                failure_class="authorization",
                provider_status=response.status_code,
            )
        if response.status_code >= 400:
            raise EbayApiError(
                "eBay could not complete the request",
                failure_class="provider",
                provider_status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise EbayApiError(
                "eBay returned an invalid response",
                failure_class="invalid_response",
                provider_status=response.status_code,
            ) from error
        if not isinstance(payload, dict):
            raise EbayApiError(
                "eBay returned an invalid response",
                failure_class="invalid_response",
                provider_status=response.status_code,
            )
        return payload

    def identity(self, access_token: str) -> EbayIdentity:
        client_id, client_secret, _ = self._credentials()
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        response = httpx.post(
            f"https://{self.rest_host}/identity/v1/oauth2/token/introspect",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"token": access_token, "token_type_hint": "access_token"},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise EbayApiError("eBay could not identify the authorized seller")
        payload = response.json()
        user_id = payload.get("sub") if payload.get("active") is True else None
        username = payload.get("username") or user_id
        if not isinstance(user_id, str) or not isinstance(username, str):
            raise EbayApiError("eBay did not return the seller identity")
        return EbayIdentity(user_id=user_id, display_name=username)

    def category_suggestions(self, query: str) -> list[EbayCategory]:
        payload = self._application_get(
            "category_suggestions",
            "/commerce/taxonomy/v1/category_tree/0/get_category_suggestions",
            q=query,
        )
        suggestions: list[EbayCategory] = []
        for item in payload.get("categorySuggestions", [])[:10]:
            category = item.get("category", {})
            ancestors = list(reversed(item.get("categoryTreeNodeAncestors", [])))
            names = [x.get("categoryName", "") for x in ancestors]
            names.append(category.get("categoryName", ""))
            if category.get("categoryId") and category.get("categoryName"):
                suggestions.append(
                    EbayCategory(
                        category_id=category["categoryId"],
                        name=category["categoryName"],
                        path=" > ".join(name for name in names if name),
                    )
                )
        return suggestions

    def category_subtree(self, group_id: str) -> list[EbayCategory]:
        payload = self._application_get(
            "category_subtree",
            "/commerce/taxonomy/v1/category_tree/0/get_category_subtree",
            category_id=group_id,
        )
        try:
            root = payload["categorySubtreeNode"]
            if root["category"]["categoryId"] != group_id:
                raise ValueError("wrong subtree")
            categories: dict[str, EbayCategory] = {}

            def visit(node, ancestors):
                category = node["category"]
                names = [*ancestors, category["categoryName"]]
                if node.get("leafCategoryTreeNode") is True:
                    item = EbayCategory(
                        category_id=category["categoryId"],
                        name=category["categoryName"],
                        path=" > ".join(names),
                    )
                    if item.category_id in categories:
                        raise ValueError("duplicate category")
                    categories[item.category_id] = item
                else:
                    children = node["childCategoryTreeNodes"]
                    if not children:
                        raise ValueError("missing children")
                    for child in children:
                        visit(child, names)

            visit(root, [])
            if not categories:
                raise ValueError("empty subtree")
            return sorted(
                categories.values(), key=lambda item: (item.path.casefold(), item.category_id)
            )
        except (KeyError, TypeError, ValueError, RecursionError) as error:
            raise EbayApiError("eBay returned invalid category details") from error

    def category_requirements(self, category_id: str) -> CategoryRequirements:
        aspects_payload = self._application_get(
            "category_aspects",
            "/commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category",
            category_id=category_id,
        )
        conditions_payload = self._application_get(
            "category_conditions",
            "/sell/metadata/v1/marketplace/EBAY_US/get_item_condition_policies",
            filter=f"categoryIds:{{{category_id}}}",
        )
        try:
            category = EbayCategory(category_id=category_id, name=category_id, path=category_id)
            aspects = []
            for item in aspects_payload.get("aspects", []):
                constraint = item.get("aspectConstraint", {})
                name = item.get("localizedAspectName")
                if name:
                    required = constraint.get("aspectRequired") is True
                    usage = _taxonomy_enum(constraint.get("aspectUsage"), default="OPTIONAL")
                    aspects.append(
                        EbayAspect(
                            name=name,
                            required=required,
                            recommended=usage == "recommended" and not required,
                            usage=usage,
                            mode=_taxonomy_enum(
                                constraint.get("aspectMode"),
                                mapping={"SELECTION_ONLY": "selection", "FREE_TEXT": "free_text"},
                                default="FREE_TEXT",
                            ),
                            cardinality=_taxonomy_enum(
                                constraint.get("itemToAspectCardinality"), default="SINGLE"
                            ),
                            max_length=constraint.get("aspectMaxLength"),
                            data_type=_taxonomy_enum(
                                constraint.get("aspectDataType"), default="STRING"
                            ),
                            format=constraint.get("aspectFormat"),
                            advanced_data_type=(
                                _taxonomy_enum(constraint.get("aspectAdvancedDataType"))
                                if constraint.get("aspectAdvancedDataType")
                                else None
                            ),
                            applicable_to=_taxonomy_enums(constraint.get("aspectApplicableTo", [])),
                            expected_required_by_date=constraint.get("expectedRequiredByDate"),
                            values=[
                                EbayAspectValue(
                                    value=value["localizedValue"],
                                    constraints=[
                                        EbayAspectValueConstraint(
                                            aspect_name=dependency[
                                                "applicableForLocalizedAspectName"
                                            ],
                                            values=dependency["applicableForLocalizedAspectValues"],
                                        )
                                        for dependency in value.get("valueConstraints", [])
                                        if dependency.get("applicableForLocalizedAspectName")
                                        and dependency.get("applicableForLocalizedAspectValues")
                                    ],
                                )
                                for value in item.get("aspectValues", [])
                                if value.get("localizedValue")
                            ],
                        )
                    )
            conditions = []
            policies = conditions_payload.get("itemConditionPolicies", [])
            if policies:
                for item in policies[0].get("itemConditions", []):
                    conditions.append(
                        EbayCondition(
                            condition_id=int(item["conditionId"]),
                            name=item.get("conditionDescription", str(item["conditionId"])),
                        )
                    )
            return CategoryRequirements(category=category, conditions=conditions, aspects=aspects)
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise EbayApiError("eBay returned invalid category details") from error

    def upload_image(self, access_token: str, path: Path) -> MediaReference:
        with path.open("rb") as image_file:
            response = httpx.post(
                f"https://{self.media_host}/commerce/media/v1_beta/image/create_image_from_file",
                headers={"Authorization": f"Bearer {access_token}"},
                files={"image": (path.name, image_file, "image/jpeg")},
                timeout=self.timeout,
            )
        self._raise_for_user_response(response)
        payload = response.json()
        image_id = response.headers.get("location", "").rstrip("/").rsplit("/", 1)[-1]
        if not image_id:
            raise EbayApiError("eBay did not return an image ID")
        expires_at = payload.get("expirationDate")
        return MediaReference(
            image_id=image_id,
            image_url=payload["imageUrl"],
            expires_at=datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_at
            else None,
        )

    def create_draft_feed_task(self, access_token: str) -> FeedTask:
        task_response = httpx.post(
            f"https://{self.rest_host}/sell/feed/v1/task",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            json={"feedType": "FX_LISTING", "schemaVersion": "1.0"},
            timeout=self.timeout,
        )
        self._raise_for_user_response(task_response)
        task_id = task_response.headers.get("location", "").rstrip("/").rsplit("/", 1)[-1]
        if not task_id:
            raise EbayApiError("eBay did not return a Feed task")
        return FeedTask(task_id=task_id, status="CREATED")

    def upload_draft_feed(
        self,
        access_token: str,
        task_id: str,
        listing: EbayReadyListing,
        custom_label: str,
        image_urls: list[str],
    ) -> FeedTask:
        feed = build_draft_feed(listing, custom_label, image_urls)
        filename = draft_feed_filename()
        upload_response = httpx.post(
            f"https://{self.rest_host}/sell/feed/v1/task/{task_id}/upload_file",
            headers={"Authorization": f"Bearer {access_token}"},
            data={"fileName": filename},
            files={"file": (filename, feed, "text/csv")},
            timeout=self.timeout,
        )
        self._raise_for_user_response(upload_response)
        return FeedTask(task_id=task_id, status="QUEUED")

    def get_feed_task(self, access_token: str, task_id: str) -> FeedTask:
        payload = self._get(f"/sell/feed/v1/task/{task_id}", access_token)
        status = str(payload.get("status", "FAILED")).upper()
        if status not in FeedTask.model_fields["status"].annotation.__args__:
            status = "FAILED"
        return FeedTask(task_id=task_id, status=status)

    def get_feed_result(self, access_token: str, task_id: str) -> str:
        response = httpx.get(
            f"https://{self.rest_host}/sell/feed/v1/task/{task_id}/download_result_file",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=self.timeout,
        )
        self._raise_for_user_response(response)
        content = response.content
        if content.startswith(b"\x1f\x8b"):
            try:
                content = gzip.decompress(content)
            except OSError as error:
                raise EbayApiError("eBay returned an unreadable Feed result") from error
        return content.decode("utf-8-sig", errors="replace")[:100_000]

    def get_feed_input(self, access_token: str, task_id: str) -> bytes:
        response = httpx.get(
            f"https://{self.rest_host}/sell/feed/v1/task/{task_id}/download_input_file",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=self.timeout,
        )
        self._raise_for_user_response(response)
        content = response.content
        if content.startswith(b"\x1f\x8b"):
            try:
                content = gzip.decompress(content)
            except OSError as error:
                raise EbayApiError("eBay returned an unreadable Feed input") from error
        return content

    def business_policies(self, access_token: str) -> list[dict[str, str]]:
        policies = []
        policy_types = {
            "fulfillment_policy": ("fulfillmentPolicies", "fulfillmentPolicyId"),
            "payment_policy": ("paymentPolicies", "paymentPolicyId"),
            "return_policy": ("returnPolicies", "returnPolicyId"),
        }
        for kind, (collection_name, id_name) in policy_types.items():
            payload = self._get(f"/sell/account/v1/{kind}", access_token, marketplace_id="EBAY_US")
            for item in payload.get(collection_name, []):
                policies.append(
                    {
                        "type": kind,
                        "id": str(item.get(id_name, "")),
                        "name": str(item.get("name", "")),
                    }
                )
        return policies

    @staticmethod
    def _raise_for_user_response(response: httpx.Response) -> None:
        if response.status_code == 401:
            raise EbayApiError("eBay authorization expired", authorization_expired=True)
        if response.status_code >= 400:
            raise EbayApiError("eBay could not complete the request")


def build_draft_feed(listing: EbayReadyListing, custom_label: str, image_urls: list[str]) -> bytes:
    """Build one Seller Hub draft-template row with Action=Draft."""

    # Seller Hub Reports rejected a complete draft when one EPS URL contained a
    # tilde. Omit the complete URL because deleting the character would corrupt
    # the EPS image identifier. Draft photos are optional and can be added later.
    seller_hub_image_urls = [url for url in image_urls if "~" not in url]
    output = io.StringIO(newline="")
    fieldnames = [
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
    ]
    csv_writer = csv.writer(output, lineterminator="\r\n")
    csv_writer.writerows(
        (
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
        )
    )
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\r\n")
    writer.writeheader()
    description = _draft_html_text(listing.description)
    if listing.item_specifics:
        specifics = "<br>".join(
            f"{_draft_html_text(item.name)}: {_draft_html_text(', '.join(item.values))}"
            for item in listing.item_specifics
        )
        description = f"{description}<br><br>Item specifics:<br>{specifics}"
    row = {
        DRAFT_FEED_ACTION_HEADER: "Draft",
        # The successful control leaves this optional field empty. Populated variants
        # returned BAF.Error.5, so keep the stable identifier inside the app.
        "Custom label (SKU)": "",
        "Category ID": listing.category_id,
        "Title": listing.title,
        "UPC": "",
        "Price": f"{listing.price:.2f}",
        "Quantity": "1",
        "Item photo URL": "|".join(seller_hub_image_urls),
        "Condition ID": "NEW" if listing.condition_id == 1000 else "USED",
        "Description": description,
        "Format": "FixedPrice",
    }
    writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _draft_html_text(value: str) -> str:
    """Replace physical line breaks with the HTML breaks required by Seller Hub."""

    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
