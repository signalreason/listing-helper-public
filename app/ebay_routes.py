"""HTTP routes for safe eBay connection, draft transfer, and gated live listings."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from fastapi import File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import ValidationError

from app.accounts import User
from app.ebay_api import EbayApiError, build_draft_feed, draft_feed_filename
from app.ebay_models import (
    BusinessPolicies,
    CategoryRequirements,
    EbayCategory,
    EbayItemSpecific,
    EbayReadyListing,
    EndRequest,
    ReviseRequest,
    TradingResult,
)
from app.ebay_security import EbaySecurityError, deletion_challenge_response
from app.ebay_store import (
    DraftChangedDuringPublication,
    DraftPublicationInProgress,
    EbayAccountAlreadyConnected,
    EbayConnection,
    EbayStoreUnavailable,
    StoredMedia,
    StoredTransfer,
)
from app.images import MAX_PHOTOS, ImageInputError, prepare_image
from app.item_specifics import validate_item_specifics
from app.models import (
    DraftEbayCategory,
    DraftEbayCondition,
    ListingDraft,
    PackageDimensions,
    PackageWeight,
)

CATEGORY_GROUPS = (
    {"group_id": "260012", "name": "Men"},
    {"group_id": "260010", "name": "Women"},
    {"group_id": "171146", "name": "Kids"},
)

SELLER_HUB_DRAFTS_URL = "https://www.ebay.com/sh/lst/drafts"
SELLER_HUB_ACTIVE_URL = "https://www.ebay.com/sh/lst/active"
SELLER_HUB_UPLOADS_URL = "https://www.ebay.com/sh/reports/uploads"
MANUAL_UPLOAD_READY = "READY_FOR_UPLOAD"
TERMINAL_TRANSFER_STATUSES = {
    "COMPLETED",
    "COMPLETED_WITH_ERROR",
    "FAILED",
    MANUAL_UPLOAD_READY,
}
OAUTH_LOGGER = logging.getLogger("app.ebay.oauth")
FEED_LOGGER = logging.getLogger("app.ebay.feed")


def install_ebay_routes(
    application,
    *,
    api_problem,
    require_user: Callable[[Request], User],
    require_same_origin: Callable[[Request], None],
) -> None:
    def store():
        return application.state.ebay_store

    def connection_for(user: User):
        try:
            connection = store().get_connection(user.id)
        except EbayStoreUnavailable:
            raise api_problem(
                503, "ebay_unavailable", "The eBay connection is unavailable."
            ) from None
        if connection is None:
            raise api_problem(409, "ebay_not_connected", "Connect your eBay account first.")
        if connection.status == "expired":
            raise api_problem(409, "ebay_reconnect_required", "Reconnect your eBay account.")
        return connection

    def access_token(user: User) -> str:
        connection = connection_for(user)
        try:
            refresh_token = application.state.ebay_token_cipher.decrypt(
                connection.encrypted_refresh_token
            )
            return application.state.ebay_gateway.user_access_token(refresh_token)
        except EbaySecurityError:
            raise api_problem(
                503, "ebay_unavailable", "The eBay connection is unavailable."
            ) from None
        except EbayApiError as error:
            if error.authorization_expired:
                store().mark_authorization_expired(user.id)
                raise api_problem(
                    409, "ebay_reconnect_required", "Reconnect your eBay account."
                ) from None
            raise api_problem(
                502, "ebay_request_failed", "eBay did not complete the request."
            ) from None

    def listing_for(user: User, listing_id: str):
        try:
            listing = store().get_listing(user.id, listing_id)
        except EbayStoreUnavailable:
            raise api_problem(503, "ebay_unavailable", "The eBay data is unavailable.") from None
        if listing is None:
            raise api_problem(404, "listing_not_found", "That listing does not exist.")
        return listing

    def media_for(listing_id: str):
        return store().list_media(listing_id)

    def require_direct_publish() -> None:
        if os.environ.get("EBAY_DIRECT_PUBLISH_ENABLED", "false").lower() != "true":
            raise api_problem(
                404,
                "direct_publish_disabled",
                "Direct eBay publishing is not enabled.",
            )

    @application.get("/api/ebay/connection")
    async def ebay_connection_status(request: Request) -> dict[str, object]:
        user = require_user(request)
        try:
            connection = await run_in_threadpool(store().get_connection, user.id)
        except EbayStoreUnavailable:
            raise api_problem(
                503, "ebay_unavailable", "The eBay connection is unavailable."
            ) from None
        return {
            "configured": application.state.ebay_gateway.configured()
            and application.state.ebay_token_cipher.configured(),
            "connected": bool(connection and connection.status == "connected"),
            "status": connection.status if connection else "disconnected",
            "display_name": connection.display_name if connection else None,
            "direct_publish_enabled": os.environ.get("EBAY_DIRECT_PUBLISH_ENABLED", "false").lower()
            == "true",
        }

    @application.post("/api/ebay/oauth/start")
    async def start_ebay_oauth(request: Request) -> dict[str, str]:
        require_same_origin(request)
        user = require_user(request)
        try:
            if not application.state.ebay_token_cipher.configured():
                raise EbaySecurityError("eBay token encryption is not configured")
            state = application.state.ebay_oauth_state.create(user.id)
            return {"authorization_url": application.state.ebay_gateway.authorization_url(state)}
        except (EbayApiError, EbaySecurityError):
            raise api_problem(
                503, "ebay_not_configured", "eBay connection is not configured."
            ) from None

    @application.get("/api/ebay/oauth/callback")
    async def finish_ebay_oauth(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ):
        user = require_user(request)
        if error or not code or not state:
            OAUTH_LOGGER.warning("eBay OAuth callback failed category=cancelled")
            return RedirectResponse("/?ebay=connection-cancelled", status_code=303)
        try:
            application.state.ebay_oauth_state.verify(state, user.id)
        except EbaySecurityError:
            OAUTH_LOGGER.warning("eBay OAuth callback failed category=state_invalid")
            return RedirectResponse("/?ebay=connection-failed", status_code=303)
        try:
            grant = await run_in_threadpool(application.state.ebay_gateway.exchange_code, code)
        except EbayApiError:
            OAUTH_LOGGER.warning("eBay OAuth callback failed category=token_exchange_failed")
            return RedirectResponse("/?ebay=connection-failed", status_code=303)
        try:
            identity = await run_in_threadpool(
                application.state.ebay_gateway.identity, grant.access_token
            )
        except EbayApiError:
            OAUTH_LOGGER.warning("eBay OAuth callback failed category=identity_failed")
            return RedirectResponse("/?ebay=connection-failed", status_code=303)
        try:
            encrypted_token = application.state.ebay_token_cipher.encrypt(grant.refresh_token)
        except EbaySecurityError:
            OAUTH_LOGGER.warning("eBay OAuth callback failed category=token_encryption_failed")
            return RedirectResponse("/?ebay=connection-failed", status_code=303)
        try:
            await run_in_threadpool(
                store().connect,
                EbayConnection(
                    user_id=user.id,
                    ebay_user_id=identity.user_id,
                    display_name=identity.display_name,
                    encrypted_refresh_token=encrypted_token,
                    status="connected",
                    connected_at=datetime.now(UTC),
                ),
            )
        except EbayAccountAlreadyConnected:
            OAUTH_LOGGER.warning("eBay OAuth callback failed category=account_already_connected")
            return RedirectResponse("/?ebay=already-connected", status_code=303)
        except EbayStoreUnavailable:
            OAUTH_LOGGER.warning("eBay OAuth callback failed category=persistence_failed")
            return RedirectResponse("/?ebay=connection-failed", status_code=303)
        OAUTH_LOGGER.info("eBay OAuth callback completed category=connected")
        return RedirectResponse("/?ebay=connected", status_code=303)

    @application.delete("/api/ebay/connection", status_code=204)
    async def disconnect_ebay(request: Request):
        require_same_origin(request)
        user = require_user(request)
        try:
            await run_in_threadpool(store().disconnect, user.id)
        except EbayStoreUnavailable:
            raise api_problem(
                503, "ebay_unavailable", "The eBay connection is unavailable."
            ) from None
        return Response(status_code=204)

    category_locks = {group["group_id"]: threading.Lock() for group in CATEGORY_GROUPS}

    @application.get("/api/ebay/category-groups")
    def ebay_category_groups(request: Request):
        require_user(request)
        return {"groups": CATEGORY_GROUPS}

    @application.get("/api/ebay/category-groups/{group_id}/categories")
    def ebay_group_categories(request: Request, group_id: str):
        require_user(request)
        if group_id not in category_locks:
            raise api_problem(422, "invalid_category_group", "Choose a category group.")
        gateway = application.state.ebay_gateway
        key = f"{gateway.environment}:0:{group_id}"
        try:
            with category_locks[group_id]:
                categories = store().get_category_list(key)
                if categories is None:
                    categories = [
                        item.model_dump(mode="json") for item in gateway.category_subtree(group_id)
                    ]
                    if not categories:
                        raise EbayApiError("eBay returned no group categories")
                    store().save_category_list(key, categories)
                categories = [
                    EbayCategory.model_validate(item).model_dump(mode="json") for item in categories
                ]
        except (EbayApiError, ValidationError):
            raise api_problem(
                502,
                "ebay_request_failed",
                "Category loading failed. Select the group again to retry.",
            ) from None
        except EbayStoreUnavailable:
            raise api_problem(
                503, "ebay_unavailable", "Stored categories are unavailable. Try again."
            ) from None
        return {"group_id": group_id, "categories": categories}

    @application.get("/api/ebay/categories")
    async def ebay_categories(request: Request, q: str) -> dict[str, object]:
        require_user(request)
        if not q.strip() or len(q) > 200:
            raise api_problem(422, "invalid_category_search", "Enter a category search.")
        try:
            items = await run_in_threadpool(
                application.state.ebay_gateway.category_suggestions, q.strip()
            )
        except EbayApiError:
            raise api_problem(502, "ebay_request_failed", "eBay category search failed.") from None
        return {"categories": [item.model_dump(mode="json") for item in items]}

    @application.get("/api/ebay/categories/{category_id}/requirements")
    async def ebay_category_requirements(request: Request, category_id: str):
        require_user(request)
        if not category_id.isdigit():
            raise api_problem(422, "invalid_category", "Choose a valid eBay category.")
        try:
            result = await run_in_threadpool(
                application.state.ebay_gateway.category_requirements, category_id
            )
        except EbayApiError:
            raise api_problem(502, "ebay_request_failed", "eBay category details failed.") from None
        return result.model_dump(mode="json")

    @application.post("/api/ebay/draft-transfers")
    async def prepare_seller_hub_file(
        request: Request,
        photos: Annotated[list[UploadFile] | None, File()] = None,
    ):
        uploads = photos or []
        try:
            require_same_origin(request)
            require_user(request)
            raise api_problem(
                410,
                "seller_hub_csv_retired",
                "New CSV handoffs are retired. Use Publish live on eBay.",
            )
        finally:
            for upload in uploads:
                await upload.close()

    @application.get("/api/ebay/draft-transfers/{transfer_id}")
    async def get_seller_hub_transfer(request: Request, transfer_id: str):
        user = require_user(request)
        try:
            transfer = await run_in_threadpool(store().get_transfer, user.id, transfer_id)
        except EbayStoreUnavailable:
            raise api_problem(503, "ebay_unavailable", "The eBay data is unavailable.") from None
        if transfer is None:
            raise api_problem(404, "transfer_not_found", "That transfer does not exist.")
        if transfer.status not in TERMINAL_TRANSFER_STATUSES:
            token = await run_in_threadpool(access_token, user)
            try:
                task = await run_in_threadpool(
                    application.state.ebay_gateway.get_feed_task, token, transfer.task_id
                )
                error = None
                if task.status in {"COMPLETED_WITH_ERROR", "FAILED"}:
                    result = await run_in_threadpool(
                        application.state.ebay_gateway.get_feed_result, token, transfer.task_id
                    )
                    error = _safe_feed_error(result)
                    input_match: bool | None = None
                    try:
                        received = await run_in_threadpool(
                            application.state.ebay_gateway.get_feed_input,
                            token,
                            transfer.task_id,
                        )
                        listing = await run_in_threadpool(
                            store().get_listing, user.id, transfer.listing_id
                        )
                        current_media = await run_in_threadpool(
                            store().list_media, transfer.listing_id
                        )
                        if listing is not None:
                            expected = build_draft_feed(
                                EbayReadyListing.model_validate(listing.draft),
                                listing.custom_label,
                                [item.image_url for item in current_media],
                            )
                            input_match = received == expected
                    except (EbayApiError, EbayStoreUnavailable, ValidationError):
                        pass
                    FEED_LOGGER.warning(
                        "event=feed_rejected codes=%s input_match=%s",
                        ",".join(_feed_error_codes(result)) or "unknown",
                        input_match,
                    )
                await run_in_threadpool(store().update_transfer, transfer.id, task.status, error)
                transfer = await run_in_threadpool(store().get_transfer, user.id, transfer.id)
            except EbayApiError:
                raise api_problem(
                    502, "ebay_status_failed", "eBay transfer status is not available yet."
                ) from None
        return _transfer_json(transfer, media_for(transfer.listing_id))

    @application.get("/api/ebay/draft-transfers/{transfer_id}/file")
    async def download_seller_hub_file(request: Request, transfer_id: str):
        user = require_user(request)
        try:
            transfer = await run_in_threadpool(store().get_transfer, user.id, transfer_id)
            if transfer is None:
                raise api_problem(404, "transfer_not_found", "That transfer does not exist.")
            listing = await run_in_threadpool(store().get_listing, user.id, transfer.listing_id)
            if listing is None:
                raise api_problem(404, "listing_not_found", "That listing does not exist.")
            media = await run_in_threadpool(store().list_media, transfer.listing_id)
            feed = build_draft_feed(
                EbayReadyListing.model_validate(listing.draft),
                listing.custom_label,
                [item.image_url for item in media],
            )
        except EbayStoreUnavailable:
            raise api_problem(503, "ebay_unavailable", "The eBay data is unavailable.") from None
        except ValidationError:
            raise api_problem(
                422, "invalid_ebay_listing", "Review the eBay listing fields."
            ) from None
        return Response(
            content=feed,
            media_type="text/csv; charset=utf-8",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": f'attachment; filename="{draft_feed_filename()}"',
            },
        )

    @application.get("/api/ebay/business-policies")
    async def get_business_policies(request: Request):
        require_direct_publish()
        user = require_user(request)
        token = await run_in_threadpool(access_token, user)
        try:
            policies = await run_in_threadpool(
                application.state.ebay_gateway.business_policies, token
            )
        except EbayApiError:
            raise api_problem(
                502, "ebay_request_failed", "eBay policies are unavailable."
            ) from None
        return {"policies": policies}

    @application.post("/api/ebay/live-listing-preparations", status_code=201)
    async def prepare_live_listing(
        request: Request,
        draft_id: Annotated[str, Form()],
        draft_revision: Annotated[int, Form()],
        fulfillment_policy_id: Annotated[str, Form()],
        payment_policy_id: Annotated[str, Form()],
        return_policy_id: Annotated[str, Form()],
        package_weight_pounds: Annotated[str, Form()],
        package_weight_ounces: Annotated[str, Form()],
        package_length: Annotated[str, Form()],
        package_width: Annotated[str, Form()],
        package_height: Annotated[str, Form()],
        recovery_preparation_id: Annotated[str | None, Form()] = None,
        confirm_no_ebay_draft: Annotated[bool, Form()] = False,
        photos: Annotated[list[UploadFile] | None, File()] = None,
    ):
        """Save an eBay-ready listing and its photos without publishing it."""

        uploads = photos or []
        try:
            require_same_origin(request)
            require_direct_publish()
            user = require_user(request)
            try:
                stable_id = uuid.UUID(draft_id)
            except ValueError:
                raise api_problem(
                    422, "invalid_ebay_listing", "Review the eBay listing fields."
                ) from None
            saved_draft = await run_in_threadpool(store().get_draft, user.id, str(stable_id))
            if saved_draft is None:
                raise api_problem(404, "draft_not_found", "That saved draft does not exist.")
            if saved_draft.revision != draft_revision:
                raise api_problem(
                    409,
                    "draft_changed",
                    "The draft changed in another tab. Reload the draft before you publish it.",
                )
            if not os.environ.get("EBAY_ITEM_POSTAL_CODE", "").strip():
                raise api_problem(
                    503,
                    "ebay_postal_code_missing",
                    "The shared eBay item postal code is not configured.",
                )
            protected_recovery = None
            if recovery_preparation_id:
                candidate = await run_in_threadpool(store().get_listing, user.id, str(stable_id))
                if (
                    candidate
                    and candidate.state == "publishing"
                    and candidate.preparation_id == recovery_preparation_id
                    and candidate.listing_draft_id == saved_draft.id
                    and candidate.listing_draft_revision == saved_draft.revision
                ):
                    protected_recovery = candidate
            if protected_recovery:
                listing_snapshot = protected_recovery.draft
                token = await run_in_threadpool(access_token, user)
            else:
                if saved_draft.draft.get("generation_required", False):
                    raise api_problem(
                        409,
                        "generation_required",
                        "Category changed. Select Generate listing again before publication.",
                    )
                try:
                    package_weight = PackageWeight(
                        pounds=package_weight_pounds,
                        ounces=package_weight_ounces,
                    )
                except ValidationError:
                    raise api_problem(
                        422,
                        "invalid_package_weight",
                        "Enter a package weight greater than zero. Use 0 to 15 ounces.",
                    ) from None
                try:
                    package_dimensions = PackageDimensions(
                        length=package_length,
                        width=package_width,
                        height=package_height,
                    )
                except ValidationError:
                    raise api_problem(
                        422,
                        "invalid_package_dimensions",
                        "Enter whole-number package length, width, and height greater than zero.",
                    ) from None
                try:
                    listing_input = _ebay_listing_from_draft(saved_draft).model_copy(
                        update={
                            "business_policies": BusinessPolicies(
                                fulfillment_policy_id=fulfillment_policy_id,
                                payment_policy_id=payment_policy_id,
                                return_policy_id=return_policy_id,
                            ),
                            "package_weight": package_weight,
                            "package_dimensions": package_dimensions,
                        }
                    )
                except ValidationError:
                    raise api_problem(
                        422, "invalid_ebay_listing", "Review the eBay listing fields."
                    ) from None
                _require_policies(listing_input, api_problem)
                try:
                    requirements = await run_in_threadpool(
                        application.state.ebay_gateway.category_requirements,
                        listing_input.category_id,
                    )
                except EbayApiError:
                    raise api_problem(
                        502, "ebay_request_failed", "eBay category details failed."
                    ) from None
                listing_input = _validate_item_specifics(listing_input, requirements, api_problem)
                token = await run_in_threadpool(access_token, user)
                try:
                    available_policies = await run_in_threadpool(
                        application.state.ebay_gateway.business_policies, token
                    )
                except EbayApiError:
                    raise api_problem(
                        502, "ebay_request_failed", "eBay policies are unavailable."
                    ) from None
                _validate_policies(listing_input.business_policies, available_policies, api_problem)
                listing_snapshot = listing_input.model_dump(mode="json")
            expected_photo_count = saved_draft.photo_count
            if uploads and len(uploads) != expected_photo_count:
                raise api_problem(
                    400,
                    "photo_count_changed",
                    f"Select all {expected_photo_count} photos in their original order.",
                )
            custom_label = f"LA{user.id}{stable_id.hex[:16]}"
            preparation_id = recovery_preparation_id or str(uuid.uuid4())
            try:
                transfer = await run_in_threadpool(store().get_listing_transfer, str(stable_id))
                if transfer and transfer.status == "COMPLETED":
                    raise api_problem(
                        409,
                        "seller_hub_draft_exists",
                        "This listing was already sent to Seller Hub. Publish it there.",
                    )
                if transfer and transfer.status not in {
                    MANUAL_UPLOAD_READY,
                    "COMPLETED_WITH_ERROR",
                    "FAILED",
                }:
                    raise api_problem(
                        409,
                        "seller_hub_transfer_pending",
                        "The old Seller Hub transfer is not finished. Check its status first.",
                    )
                if transfer and not confirm_no_ebay_draft:
                    raise api_problem(
                        409,
                        "legacy_seller_hub_check_required",
                        "Confirm that no Seller Hub draft exists before direct publication.",
                    )
                saved = await run_in_threadpool(
                    store().get_or_create_listing,
                    user.id,
                    str(stable_id),
                    custom_label,
                    listing_snapshot,
                    saved_draft.id,
                    saved_draft.revision,
                    preparation_id,
                )
                if transfer:
                    await run_in_threadpool(store().clear_listing_transfer, saved.id)
            except DraftChangedDuringPublication:
                raise api_problem(
                    409,
                    "draft_changed",
                    "The draft changed while publication started. Review it and try again.",
                ) from None
            except DraftPublicationInProgress:
                raise api_problem(
                    409,
                    "publication_in_progress",
                    "Another publication request is already in progress.",
                ) from None
            try:
                current_media = await _upload_missing_media(
                    application,
                    api_problem,
                    token,
                    saved.id,
                    uploads,
                    expected_photo_count,
                    saved.listing_draft_revision,
                    saved.preparation_id,
                )
            except DraftChangedDuringPublication:
                raise api_problem(
                    409,
                    "draft_changed",
                    "The draft changed while photos uploaded. Review it and try again.",
                ) from None
            except DraftPublicationInProgress:
                raise api_problem(
                    409,
                    "publication_in_progress",
                    "Another publication request is already in progress.",
                ) from None
            return {
                "listing_id": saved.id,
                "custom_label": saved.custom_label,
                "photo_count": len(current_media),
                "draft_revision": saved.listing_draft_revision,
                "preparation_id": saved.preparation_id,
            }
        finally:
            for upload in uploads:
                await upload.close()

    @application.put("/api/ebay/listings/{listing_id}/photos")
    async def replace_live_listing_photos(
        request: Request,
        listing_id: str,
        photos: Annotated[list[UploadFile] | None, File()] = None,
    ):
        uploads = photos or []
        try:
            require_same_origin(request)
            require_direct_publish()
            user = require_user(request)
            listing = listing_for(user, listing_id)
            if not 1 <= len(uploads) <= MAX_PHOTOS:
                raise api_problem(400, "photos_required", "Choose between 1 and 24 photos.")
            token = await run_in_threadpool(access_token, user)
            uploaded = await _upload_new_media(application, api_problem, token, listing.id, uploads)
            await run_in_threadpool(store().replace_media, listing.id, uploaded)
            return {"listing_id": listing.id, "photo_count": len(uploaded)}
        finally:
            for upload in uploads:
                await upload.close()

    @application.post("/api/ebay/listings/{listing_id}/verify")
    async def verify_live_listing(request: Request, listing_id: str):
        require_same_origin(request)
        require_direct_publish()
        user = require_user(request)
        listing = listing_for(user, listing_id)
        draft = EbayReadyListing.model_validate(listing.draft)
        _require_policies(draft, api_problem)
        _require_package_weight(draft, api_problem)
        _require_package_dimensions(draft, api_problem)
        token = await run_in_threadpool(access_token, user)
        result = await run_in_threadpool(
            application.state.trading.verify,
            token,
            draft,
            listing.custom_label,
            [item.image_url for item in media_for(listing.id)],
        )
        media = media_for(listing.id)
        return {
            "review": {
                "listing": draft.model_dump(mode="json"),
                "custom_label": listing.custom_label,
                "photos": [item.image_url for item in media],
                "warnings": [
                    item.model_dump(mode="json")
                    for item in result.issues
                    if item.severity == "warning"
                ],
                "expected_fees": result.fees,
            },
            "verification": result.model_dump(mode="json"),
        }

    @application.post("/api/ebay/listings/{listing_id}/publish")
    async def publish_live_listing(request: Request, listing_id: str):
        require_same_origin(request)
        require_direct_publish()
        user = require_user(request)
        listing = listing_for(user, listing_id)
        if listing.state == "live" and listing.ebay_item_id:
            return _publication_json(listing.ebay_item_id)
        try:
            publication_input = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            publication_input = {}
        prepared_revision = (
            publication_input.get("draft_revision") if isinstance(publication_input, dict) else None
        )
        if isinstance(prepared_revision, bool) or not isinstance(prepared_revision, int):
            prepared_revision = None
        prepared_id = (
            publication_input.get("preparation_id") if isinstance(publication_input, dict) else None
        )
        if not isinstance(prepared_id, str):
            prepared_id = None
        if (
            listing.listing_draft_revision is None
            or listing.preparation_id is None
            or prepared_revision != listing.listing_draft_revision
            or prepared_id != listing.preparation_id
        ):
            raise api_problem(
                409,
                "draft_changed",
                "The publication preparation changed. Review the draft and try again.",
            )
        transfer = store().get_listing_transfer(listing.id)
        if transfer:
            raise api_problem(
                409,
                "seller_hub_draft_exists",
                "This listing was sent to Seller Hub. Publish it there to avoid a duplicate.",
            )
        prior_publication_uncertain = listing.publication_attempted
        publication_uncertain = prior_publication_uncertain
        token = await run_in_threadpool(access_token, user)
        try:
            publication_lease = await run_in_threadpool(
                store().begin_listing_publication,
                listing.id,
                prepared_revision,
                prepared_id,
            )
        except DraftChangedDuringPublication:
            raise api_problem(
                409,
                "draft_changed",
                "The draft changed after publication preparation. Review it and try again.",
            ) from None
        except DraftPublicationInProgress:
            raise api_problem(
                409,
                "publication_in_progress",
                "Another publication request is already in progress.",
            ) from None
        publication_completed = False
        try:
            listing = listing_for(user, listing.id)
            prior_publication_uncertain = listing.publication_attempted
            publication_uncertain = prior_publication_uncertain
            draft = EbayReadyListing.model_validate(listing.draft)
            _require_policies(draft, api_problem)
            _require_package_weight(draft, api_problem)
            _require_package_dimensions(draft, api_problem)
            media = media_for(listing.id)
            now = datetime.now(UTC)
            if any(item.expires_at is not None and item.expires_at <= now for item in media):
                detail = (
                    "The saved eBay photo references expired. Retry after 15 minutes "
                    "to refresh them safely."
                    if prior_publication_uncertain
                    else "The saved eBay photo references expired. Try publishing again "
                    "to refresh them."
                )
                code = (
                    "ebay_photos_expired_recovery"
                    if prior_publication_uncertain
                    else "ebay_photos_expired"
                )
                raise api_problem(409, code, detail)
            urls = [item.image_url for item in media]
            try:
                verification = await run_in_threadpool(
                    application.state.trading.verify, token, draft, listing.custom_label, urls
                )
            except EbayApiError:
                code = (
                    "ebay_verification_unavailable_recovery"
                    if prior_publication_uncertain
                    else "ebay_verification_unavailable"
                )
                raise api_problem(
                    502,
                    code,
                    "eBay did not confirm verification. The listing was not published. Try again.",
                ) from None
            if not verification.acknowledged:
                code = (
                    "ebay_verification_failed_recovery"
                    if prior_publication_uncertain
                    else "ebay_verification_failed"
                )
                raise api_problem(
                    422,
                    code,
                    _trading_issue_message("eBay did not verify the listing.", verification),
                )
            try:
                await run_in_threadpool(
                    store().mark_listing_publication_attempted,
                    listing.id,
                    publication_lease,
                )
            except DraftPublicationInProgress:
                raise api_problem(
                    409,
                    "publication_in_progress",
                    "Another publication request renewed this listing. Wait for it to finish.",
                ) from None
            publication_uncertain = True
            try:
                result = await run_in_threadpool(
                    application.state.trading.publish,
                    token,
                    draft,
                    listing.custom_label,
                    urls,
                    listing.request_id,
                )
            except EbayApiError:
                raise api_problem(
                    502,
                    "ebay_publish_uncertain",
                    "eBay did not confirm publication. Retry after 15 minutes to recover "
                    "the same listing safely.",
                ) from None
            if not result.acknowledged:
                publication_uncertain = prior_publication_uncertain
                code = (
                    "ebay_publication_failed_recovery"
                    if prior_publication_uncertain
                    else "ebay_publication_failed"
                )
                raise api_problem(
                    422,
                    code,
                    _trading_issue_message("eBay rejected the listing.", result),
                )
            if not result.item_id:
                raise api_problem(
                    502,
                    "ebay_publish_uncertain",
                    "eBay did not return an item ID. Retry after 15 minutes to recover "
                    "the same listing safely.",
                )
            try:
                await run_in_threadpool(
                    store().mark_listing_live,
                    listing.id,
                    result.item_id,
                    result.revision,
                    publication_lease=publication_lease,
                )
            except DraftPublicationInProgress:
                raise api_problem(
                    409,
                    "publication_in_progress",
                    "Another publication request renewed this listing. Wait for it to finish.",
                ) from None
            publication_completed = True
            return _publication_json(result.item_id, verification, result)
        finally:
            if not publication_completed and not publication_uncertain:
                await run_in_threadpool(
                    store().abort_listing_publication,
                    listing.id,
                    publication_lease,
                )

    @application.get("/api/ebay/listings/{listing_id}")
    async def retrieve_live_listing(request: Request, listing_id: str):
        require_direct_publish()
        user = require_user(request)
        listing = listing_for(user, listing_id)
        if not listing.ebay_item_id:
            raise api_problem(409, "listing_not_live", "This listing is not live on eBay.")
        token = await run_in_threadpool(access_token, user)
        current = await run_in_threadpool(
            application.state.trading.get, token, listing.ebay_item_id
        )
        refreshed_listing = current.current_listing or EbayReadyListing.model_validate(
            listing.draft
        )
        if current.current_listing:
            await run_in_threadpool(
                store().update_listing,
                listing.id,
                refreshed_listing.model_dump(mode="json"),
                current.revision,
            )
        return {
            "listing_id": listing.id,
            "item_id": listing.ebay_item_id,
            "revision": current.revision,
            "listing": refreshed_listing.model_dump(mode="json"),
            "photos": current.current_image_urls,
            "issues": [item.model_dump() for item in current.issues],
        }

    @application.patch("/api/ebay/listings/{listing_id}")
    async def revise_live_listing(request: Request, listing_id: str, body: ReviseRequest):
        require_same_origin(request)
        require_direct_publish()
        user = require_user(request)
        listing = listing_for(user, listing_id)
        if not listing.ebay_item_id:
            raise api_problem(409, "listing_not_live", "This listing is not live on eBay.")
        _require_policies(body.listing, api_problem)
        token = await run_in_threadpool(access_token, user)
        current = await run_in_threadpool(
            application.state.trading.get, token, listing.ebay_item_id
        )
        if current.revision != body.expected_revision:
            raise api_problem(
                409,
                "ebay_listing_changed",
                "The eBay listing changed. Refresh it before you save.",
            )
        result = await run_in_threadpool(
            application.state.trading.revise,
            token,
            current.raw_item_xml,
            body.listing,
            [item.image_url for item in media_for(listing.id)]
            if body.replace_photos
            else current.current_image_urls or [item.image_url for item in media_for(listing.id)],
            str(uuid.uuid4()),
        )
        if result.acknowledged:
            await run_in_threadpool(
                store().update_listing,
                listing.id,
                body.listing.model_dump(mode="json"),
                result.revision,
            )
        return result.model_dump(mode="json")

    @application.post("/api/ebay/listings/{listing_id}/end")
    async def end_live_listing(request: Request, listing_id: str, body: EndRequest):
        require_same_origin(request)
        require_direct_publish()
        user = require_user(request)
        listing = listing_for(user, listing_id)
        if not listing.ebay_item_id:
            raise api_problem(409, "listing_not_live", "This listing is not live on eBay.")
        token = await run_in_threadpool(access_token, user)
        result = await run_in_threadpool(
            application.state.trading.end,
            token,
            listing.ebay_item_id,
            body.reason,
            str(uuid.uuid4()),
        )
        if result.acknowledged:
            await run_in_threadpool(
                store().mark_listing_live,
                listing.id,
                listing.ebay_item_id,
                result.revision,
                "ended",
            )
        return result.model_dump(mode="json")

    @application.post("/api/ebay/listings/{listing_id}/relist")
    async def relist_live_listing(request: Request, listing_id: str):
        require_same_origin(request)
        require_direct_publish()
        user = require_user(request)
        listing = listing_for(user, listing_id)
        if listing.state != "ended" or not listing.ebay_item_id:
            raise api_problem(409, "listing_not_ended", "End this listing before you relist it.")
        draft = EbayReadyListing.model_validate(listing.draft)
        _require_policies(draft, api_problem)
        token = await run_in_threadpool(access_token, user)
        current = await run_in_threadpool(
            application.state.trading.get, token, listing.ebay_item_id
        )
        relist_id = str(uuid.uuid5(uuid.UUID(listing.id), "relist-1"))
        result = await run_in_threadpool(
            application.state.trading.relist,
            token,
            current.raw_item_xml,
            draft,
            current.current_image_urls or [item.image_url for item in media_for(listing.id)],
            relist_id,
        )
        if result.acknowledged and result.item_id:
            await run_in_threadpool(
                store().mark_listing_live, listing.id, result.item_id, result.revision
            )
        return result.model_dump(mode="json")

    @application.get("/api/ebay/account-deletion", include_in_schema=False)
    async def ebay_deletion_challenge(challenge_code: str):
        token = os.environ.get("EBAY_NOTIFICATION_VERIFICATION_TOKEN")
        endpoint = os.environ.get("EBAY_NOTIFICATION_ENDPOINT_URL")
        if not token or not endpoint:
            raise api_problem(503, "ebay_notification_unavailable", "Endpoint is not configured.")
        return {"challengeResponse": deletion_challenge_response(challenge_code, token, endpoint)}

    @application.post("/api/ebay/account-deletion", include_in_schema=False)
    async def ebay_account_deletion(request: Request):
        body = await request.body()
        signature = request.headers.get("X-EBAY-SIGNATURE", "")
        if not signature or not await run_in_threadpool(
            application.state.ebay_notification_verifier.verify, body, signature
        ):
            return Response(status_code=412)
        try:
            payload = json.loads(body)
            if payload["metadata"]["topic"] != "MARKETPLACE_ACCOUNT_DELETION":
                return Response(status_code=400)
            ebay_user_id = payload["notification"]["data"]["userId"]
            if not isinstance(ebay_user_id, str) or not ebay_user_id:
                return Response(status_code=400)
            await run_in_threadpool(store().delete_ebay_user_data, ebay_user_id)
        except (KeyError, json.JSONDecodeError):
            return Response(status_code=400)
        except EbayStoreUnavailable:
            return Response(status_code=503)
        return Response(status_code=204)


def _ebay_listing_from_draft(saved) -> EbayReadyListing:
    draft = ListingDraft.model_validate(saved.draft)
    category = DraftEbayCategory.model_validate(saved.ebay_category)
    condition = DraftEbayCondition.model_validate(saved.ebay_condition)
    return EbayReadyListing(
        title=draft.title,
        category_id=category.category_id,
        category_name=category.name,
        condition_id=condition.condition_id,
        condition_description=draft.condition_description,
        description=draft.description,
        item_specifics=[
            EbayItemSpecific(name=item.name, values=item.values) for item in draft.item_specifics
        ],
        price=draft.pricing.suggested_price,
        quantity=draft.quantity,
        package_weight=draft.package_weight,
        package_dimensions=draft.package_dimensions,
    )


def _require_policies(listing: EbayReadyListing, api_problem) -> None:
    if listing.business_policies is None:
        raise api_problem(
            422,
            "business_policies_required",
            "Choose shipping, payment, and return policies before publishing.",
        )


def _require_package_weight(listing: EbayReadyListing, api_problem) -> None:
    if listing.package_weight is None:
        raise api_problem(
            422,
            "package_weight_required",
            "Enter the package weight before publishing.",
        )


def _require_package_dimensions(listing: EbayReadyListing, api_problem) -> None:
    if listing.package_dimensions is None:
        raise api_problem(
            422,
            "package_dimensions_required",
            "Enter the package length, width, and height before publishing.",
        )


def _validate_policies(
    selected: BusinessPolicies | None,
    available: list[dict[str, str]],
    api_problem,
) -> None:
    if selected is None:
        raise api_problem(
            422,
            "business_policies_required",
            "Choose shipping, payment, and return policies before publishing.",
        )
    expected = {
        "fulfillment_policy": selected.fulfillment_policy_id,
        "payment_policy": selected.payment_policy_id,
        "return_policy": selected.return_policy_id,
    }
    available_ids = {(item.get("type"), item.get("id")) for item in available}
    missing = [
        kind for kind, policy_id in expected.items() if (kind, policy_id) not in available_ids
    ]
    if missing:
        raise api_problem(
            422,
            "invalid_business_policies",
            "Choose valid shipping, payment, and return policies.",
        )


def _validate_item_specifics(
    listing: EbayReadyListing,
    requirements: CategoryRequirements,
    api_problem,
) -> EbayReadyListing:
    validation = validate_item_specifics(listing.item_specifics, requirements)
    if validation.errors:
        raise api_problem(
            422,
            "invalid_item_specifics",
            " ".join(error.message for error in validation.errors)[:2000],
            errors=[error.as_dict() for error in validation.errors],
        )
    return listing.model_copy(update={"item_specifics": validation.specifics})


def _trading_issue_message(prefix: str, result: TradingResult) -> str:
    errors = [item for item in result.issues if item.severity == "error"][:3]
    if not errors:
        return prefix
    details = []
    for issue in errors:
        detail = issue.message
        if issue.code:
            detail = f"{detail} (eBay code {issue.code})"
        details.append(detail)
    return f"{prefix} {'; '.join(details)}"[:2000]


def _publication_json(
    item_id: str,
    verification: TradingResult | None = None,
    publication: TradingResult | None = None,
) -> dict[str, object]:
    return {
        "item_id": item_id,
        "listing_url": f"https://www.ebay.com/itm/{item_id}",
        "seller_hub_url": SELLER_HUB_ACTIVE_URL,
        "verification": verification.model_dump(mode="json") if verification else None,
        "publication": publication.model_dump(mode="json") if publication else None,
    }


def _safe_feed_error(result: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", result)
    try:
        rows = csv.DictReader(io.StringIO(text))
        issues = []
        for row in rows:
            normalized = {
                re.sub(r"[^a-z]", "", key.casefold()): value
                for key, value in row.items()
                if isinstance(key, str)
            }
            message = next(
                (
                    normalized[key].strip()
                    for key in (
                        "errormessage",
                        "errordescription",
                        "longmessage",
                        "shortmessage",
                        "message",
                    )
                    if isinstance(normalized.get(key), str) and normalized[key].strip()
                ),
                "",
            )
            code = next(
                (
                    normalized[key].strip()
                    for key in ("errorcode", "code")
                    if isinstance(normalized.get(key), str) and normalized[key].strip()
                ),
                "",
            )
            issue = f"{message} (eBay code {code})" if message and code else message
            if issue and issue not in issues:
                issues.append(issue)
            if len(issues) == 3:
                break
    except (csv.Error, TypeError):
        issues = []
    if issues:
        message = (
            f"eBay rejected the draft: {'; '.join(issues)} Correct the listed fields and try again."
        )
        return message[:2000]
    return "eBay rejected the draft but did not identify a field. Try again."


def _feed_error_codes(result: str) -> list[str]:
    try:
        rows = csv.DictReader(io.StringIO(result))
        codes = []
        for row in rows:
            normalized = {
                re.sub(r"[^a-z]", "", key.casefold()): value
                for key, value in row.items()
                if isinstance(key, str)
            }
            code = next(
                (
                    normalized[key].strip()
                    for key in ("errorcode", "code")
                    if isinstance(normalized.get(key), str) and normalized[key].strip()
                ),
                "",
            )
            if code and code not in codes:
                codes.append(code[:80])
            if len(codes) == 3:
                break
        return codes
    except (csv.Error, TypeError):
        return []


async def _upload_new_media(application, api_problem, token, listing_id, uploads):
    uploaded_media = []
    with TemporaryDirectory(prefix="ebay-live-photos-") as temporary_directory:
        workdir = Path(temporary_directory)
        for position, upload in enumerate(uploads, start=1):
            upload.file.seek(0)
            try:
                prepared, _ = await run_in_threadpool(prepare_image, upload.file, workdir, position)
            except ImageInputError as error:
                raise api_problem(error.status_code, error.code, error.message) from None
            try:
                media = await run_in_threadpool(
                    application.state.ebay_gateway.upload_image, token, prepared.path
                )
            except EbayApiError:
                raise api_problem(
                    502,
                    "ebay_photo_upload_failed",
                    f"eBay did not accept photo {position}. The saved photos did not change.",
                ) from None
            uploaded_media.append(
                StoredMedia(
                    listing_id=listing_id,
                    position=position,
                    image_id=media.image_id,
                    image_url=media.image_url,
                    expires_at=media.expires_at,
                )
            )
    return uploaded_media


async def _upload_missing_media(
    application,
    api_problem,
    token: str,
    listing_id: str,
    uploads: list[UploadFile],
    expected_photo_count: int,
    expected_draft_revision: int | None,
    expected_preparation_id: str | None,
) -> list[StoredMedia]:
    current_media = {
        item.position: item
        for item in await run_in_threadpool(application.state.ebay_store.list_media, listing_id)
    }
    now = datetime.now(UTC)
    with TemporaryDirectory(prefix="ebay-live-photos-") as temporary_directory:
        workdir = Path(temporary_directory)
        for position, upload in enumerate(uploads, start=1):
            existing = current_media.get(position)
            if existing and (existing.expires_at is None or existing.expires_at > now):
                continue
            upload.file.seek(0)
            try:
                prepared, _ = await run_in_threadpool(prepare_image, upload.file, workdir, position)
            except ImageInputError as error:
                raise api_problem(error.status_code, error.code, error.message) from None
            try:
                uploaded = await run_in_threadpool(
                    application.state.ebay_gateway.upload_image, token, prepared.path
                )
            except EbayApiError:
                raise api_problem(
                    502,
                    "ebay_photo_upload_failed",
                    f"eBay did not accept photo {position}. You can safely try again.",
                ) from None
            media = StoredMedia(
                listing_id=listing_id,
                position=position,
                image_id=uploaded.image_id,
                image_url=uploaded.image_url,
                expires_at=uploaded.expires_at,
            )
            await run_in_threadpool(
                application.state.ebay_store.save_media,
                media,
                expected_draft_revision,
                expected_preparation_id,
            )
            current_media[position] = media
    expected_positions = range(1, expected_photo_count + 1)
    now = datetime.now(UTC)
    if not all(
        position in current_media
        and (current_media[position].expires_at is None or current_media[position].expires_at > now)
        for position in expected_positions
    ):
        raise api_problem(
            400,
            "photos_required",
            f"Select all {expected_photo_count} photos in their original order.",
        )
    return [current_media[position] for position in expected_positions]


def _transfer_json(transfer: StoredTransfer, media: list[StoredMedia]) -> JSONResponse:
    expiry_values = [item.expires_at for item in media if item.expires_at]
    earliest_expiry = min(expiry_values) if expiry_values else None
    payload = {
        "id": transfer.id,
        "listing_id": transfer.listing_id,
        "task_id": transfer.task_id,
        "status": transfer.status,
        "error": transfer.error,
        "download_url": (
            f"/api/ebay/draft-transfers/{transfer.id}/file"
            if transfer.status in {"COMPLETED_WITH_ERROR", "FAILED", MANUAL_UPLOAD_READY}
            else None
        ),
        "seller_hub_upload_url": (
            SELLER_HUB_UPLOADS_URL if transfer.status == MANUAL_UPLOAD_READY else None
        ),
        "seller_hub_url": SELLER_HUB_DRAFTS_URL if transfer.status == "COMPLETED" else None,
        "photo_expires_at": earliest_expiry.isoformat() if earliest_expiry else None,
        "photos_expired": bool(earliest_expiry and earliest_expiry <= datetime.now(UTC)),
        "updated_at": transfer.updated_at.isoformat(),
    }
    return JSONResponse(
        status_code=200 if transfer.status in TERMINAL_TRANSFER_STATUSES else 202,
        content=payload,
    )
