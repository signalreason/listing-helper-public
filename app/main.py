"""FastAPI entry point for the single-service product."""

from __future__ import annotations

import logging
import os
import uuid
from html import escape
from pathlib import Path
from string import Template
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Annotated

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.accounts import (
    AccountLimitReached,
    AccountStore,
    AccountStoreUnavailable,
    EmailAlreadyExists,
    PostgresAccountStore,
    SetupAlreadyComplete,
    User,
)
from app.api_models import CreateUserRequest, LoginRequest, SetupRequest, UserUpdateRequest
from app.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    Passwords,
    SessionManager,
    authenticated_user,
    generate_password,
    normalize_email,
    request_is_same_origin,
    setup_token_matches,
)
from app.ebay_api import EbayApiError, EbayGateway, EbayHttpGateway
from app.ebay_routes import install_ebay_routes
from app.ebay_security import (
    EbayPublicKeyProvider,
    EbaySecurityError,
    NotificationVerifier,
    OAuthState,
    TokenCipher,
)
from app.ebay_store import (
    DraftPublicationInProgress,
    DraftRevisionConflict,
    EbayStore,
    EbayStoreUnavailable,
    PostgresEbayStore,
)
from app.generation import (
    CategorySchemaUnsupported,
    GenerationFailed,
    GenerationUnavailable,
    ListingGenerator,
    OpenAIListingGenerator,
    category_output_model,
    clean_generated_specifics,
)
from app.images import (
    MAX_NOTE_CHARACTERS,
    MAX_PHOTOS,
    ImageInputError,
    PreparedImage,
    prepare_image,
)
from app.limits import GenerationLimits, LimitReached, LoginLimits
from app.models import (
    DraftEbayCategory,
    DraftEbayCondition,
    GenerationResponse,
    ImageWarning,
    ListingDraft,
    PublicationRecoveryPolicies,
    SavedDraftListResponse,
    SavedDraftResponse,
    SavedDraftSummary,
    SavedDraftUpdate,
)
from app.trading_api import TradingApiAdapter

STATIC_DIR = Path(__file__).parent / "static"
ACCESS_LOGGER = logging.getLogger("app.access")
DRAFT_STORE_LOGGER = logging.getLogger("app.draft_store")
HEALTH_LOGGER = logging.getLogger("app.health")
LIVE_PREPARATION_PATH = "/api/ebay/live-listing-preparations"
PACKAGE_DIMENSION_FORM_FIELDS = frozenset({"package_length", "package_width", "package_height"})


class ApiProblem(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
        self.errors = errors or []


def _user_json(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "email": user.email,
        "is_owner": user.is_owner,
        "enabled": user.enabled,
    }


def _missing_body_fields(error: RequestValidationError) -> set[str]:
    fields = set()
    for item in error.errors():
        location = item.get("loc", ())
        if item.get("type") == "missing" and len(location) == 2 and location[0] == "body":
            fields.add(str(location[1]))
    return fields


def _log_draft_store_failure(operation: str, draft_id: str, error: EbayStoreUnavailable) -> None:
    DRAFT_STORE_LOGGER.warning(
        "operation=%s draft_id=%s outcome=failure error_class=%s sqlstate=%s",
        operation,
        draft_id,
        error.error_class,
        error.sqlstate or "none",
    )


def create_app(
    generator: ListingGenerator | None = None,
    account_store: AccountStore | None = None,
    ebay_gateway: EbayGateway | None = None,
    ebay_store: EbayStore | None = None,
    *,
    session_secret: str | None = None,
    cookie_secure: bool | None = None,
    ebay_token_cipher: TokenCipher | None = None,
    ebay_notification_verifier: NotificationVerifier | None = None,
    trading_adapter: TradingApiAdapter | None = None,
) -> FastAPI:
    application = FastAPI(title="eBay Listing Assistant", docs_url=None, redoc_url=None)
    application.state.generator = generator or OpenAIListingGenerator()
    application.state.accounts = account_store or PostgresAccountStore()
    application.state.passwords = Passwords()
    application.state.sessions = SessionManager(session_secret)
    application.state.generation_limits = GenerationLimits()
    application.state.login_limits = LoginLimits()
    application.state.ebay_gateway = ebay_gateway or EbayHttpGateway()
    application.state.ebay_store = ebay_store or PostgresEbayStore()
    application.state.ebay_token_cipher = ebay_token_cipher or TokenCipher()
    application.state.ebay_oauth_state = OAuthState(session_secret)
    environment = getattr(application.state.ebay_gateway, "environment", "production")
    application.state.ebay_notification_verifier = (
        ebay_notification_verifier
        or NotificationVerifier(EbayPublicKeyProvider(environment=environment))
    )
    application.state.trading = trading_adapter or TradingApiAdapter(environment=environment)
    application.state.cookie_secure = (
        cookie_secure
        if cookie_secure is not None
        else os.environ.get("SESSION_COOKIE_SECURE", "true").lower() != "false"
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.middleware("http")
    async def prevent_stale_browser_assets(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/") or response.headers.get(
            "content-type", ""
        ).startswith("text/html"):
            # Buildpack file dates can be fixed across releases. Do not let a
            # browser infer a long freshness lifetime for changing UI assets.
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.middleware("http")
    async def safe_access_log(request: Request, call_next):
        started_at = monotonic()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            ACCESS_LOGGER.info(
                "%s %s %d %.3fs",
                request.method,
                request.url.path,
                status_code,
                monotonic() - started_at,
            )

    @application.exception_handler(ApiProblem)
    async def api_problem_handler(request: Request, error: ApiProblem) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            media_type="application/problem+json",
            headers=error.headers,
            content={
                "type": f"https://listing-helper.invalid/problems/{error.code}",
                "title": error.message,
                "status": error.status_code,
                "detail": error.message,
                "code": error.code,
                "instance": request.url.path,
                **({"errors": error.errors} if error.errors else {}),
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_problem_handler(request: Request, error: RequestValidationError):
        missing_fields = _missing_body_fields(error)
        if (
            request.url.path == LIVE_PREPARATION_PATH
            and missing_fields
            and missing_fields <= PACKAGE_DIMENSION_FORM_FIELDS
        ):
            return await api_problem_handler(
                request,
                ApiProblem(
                    422,
                    "stale_publication_page",
                    "Reload this page, reopen the saved draft, then enter package length, "
                    "width, and height.",
                ),
            )
        return await api_problem_handler(
            request,
            ApiProblem(422, "invalid_request", "Check the information you entered and try again."),
        )

    @application.exception_handler(EbayApiError)
    async def ebay_api_error_handler(request: Request, error: EbayApiError):
        problem = (
            ApiProblem(409, "ebay_reconnect_required", "Reconnect your eBay account.")
            if error.authorization_expired
            else ApiProblem(502, "ebay_request_failed", "eBay did not complete the request.")
        )
        return await api_problem_handler(request, problem)

    @application.exception_handler(EbayStoreUnavailable)
    @application.exception_handler(EbaySecurityError)
    async def ebay_local_error_handler(request: Request, _error: Exception):
        return await api_problem_handler(
            request,
            ApiProblem(503, "ebay_unavailable", "The eBay connection is unavailable."),
        )

    def accounts() -> AccountStore:
        return application.state.accounts

    def saved_draft_json(value, listing=None) -> SavedDraftResponse:
        item_id = listing.ebay_item_id if listing and listing.ebay_item_id else None
        recovery_pending = bool(listing and listing.state == "publishing")
        recovery_policies = None
        if recovery_pending:
            recovery_policies = PublicationRecoveryPolicies.model_validate(
                listing.draft.get("business_policies")
            )
        return SavedDraftResponse(
            id=value.id,
            revision=value.revision,
            draft=ListingDraft.model_validate(value.draft),
            ebay_category=(
                DraftEbayCategory.model_validate(value.ebay_category)
                if value.ebay_category
                else None
            ),
            ebay_condition=(
                DraftEbayCondition.model_validate(value.ebay_condition)
                if value.ebay_condition
                else None
            ),
            photo_count=value.photo_count,
            updated_at=value.updated_at,
            status="published" if item_id else "draft",
            ebay_item_id=item_id,
            listing_url=f"https://www.ebay.com/itm/{item_id}" if item_id else None,
            seller_hub_url="https://www.ebay.com/sh/lst/active" if item_id else None,
            publication_recovery_pending=recovery_pending,
            publication_recovery_policies=recovery_policies,
            publication_recovery_preparation_id=(
                listing.preparation_id if recovery_pending else None
            ),
        )

    def valid_draft_id(draft_id: str) -> str:
        try:
            return str(uuid.UUID(draft_id))
        except ValueError:
            raise ApiProblem(404, "draft_not_found", "That saved draft does not exist.") from None

    def current_user(request: Request) -> User | None:
        try:
            return authenticated_user(request, accounts(), application.state.sessions)
        except (AccountStoreUnavailable, RuntimeError):
            raise ApiProblem(
                503,
                "service_unavailable",
                "The app is temporarily unavailable. Please try again shortly.",
            ) from None

    def require_user(request: Request) -> User:
        user = current_user(request)
        if user is None:
            raise ApiProblem(401, "authentication_required", "Please sign in to continue.")
        return user

    def require_owner(request: Request) -> User:
        user = require_user(request)
        if not user.is_owner:
            raise ApiProblem(403, "owner_required", "Only the owner can manage users.")
        return user

    def require_same_origin(request: Request) -> None:
        if not request_is_same_origin(request):
            raise ApiProblem(403, "invalid_origin", "Reload the page and try again.")

    def set_session_cookie(response: JSONResponse, user: User) -> None:
        try:
            token = application.state.sessions.create(user)
        except RuntimeError:
            raise ApiProblem(
                503,
                "service_unavailable",
                "The app is temporarily unavailable. Please try again shortly.",
            ) from None
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=application.state.cookie_secure,
            samesite="lax",
            path="/",
        )

    @application.get("/healthz", include_in_schema=False)
    async def health() -> JSONResponse:
        try:
            await run_in_threadpool(accounts().count_users)
        except AccountStoreUnavailable:
            HEALTH_LOGGER.warning(
                "operation=healthcheck component=accounts outcome=failure error_class=database"
            )
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        try:
            await run_in_threadpool(application.state.ebay_store.healthcheck)
        except EbayStoreUnavailable as error:
            HEALTH_LOGGER.warning(
                "operation=healthcheck component=listing_store outcome=failure "
                "error_class=%s sqlstate=%s",
                error.error_class,
                error.sqlstate or "none",
            )
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ok"})

    @application.get("/", include_in_schema=False)
    async def index(request: Request):
        if current_user(request) is None:
            return RedirectResponse("/login", status_code=303)
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/login", include_in_schema=False)
    async def login_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "login.html")

    @application.api_route("/privacy", methods=["GET", "HEAD"], include_in_schema=False)
    async def privacy_page() -> HTMLResponse:
        operator_name = os.environ.get("APP_OPERATOR_NAME", "").strip()
        try:
            privacy_email = TypeAdapter(EmailStr).validate_python(
                os.environ.get("APP_PRIVACY_EMAIL", "")
            )
        except ValidationError:
            privacy_email = ""
        if not operator_name or not privacy_email:
            return HTMLResponse(
                "<!doctype html><html lang='en'><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>Privacy policy unavailable</title>"
                "<h1>Privacy policy unavailable</h1>"
                "<p>The app operator must configure the privacy contact before use.</p></html>",
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        page = Template((STATIC_DIR.parent / "privacy.html").read_text(encoding="utf-8"))
        return HTMLResponse(
            page.substitute(
                operator_name=escape(operator_name), privacy_email=escape(privacy_email)
            ),
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/admin", include_in_schema=False)
    async def admin_page(request: Request):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not user.is_owner:
            return RedirectResponse("/", status_code=303)
        return FileResponse(STATIC_DIR / "admin.html")

    @application.get("/api/session")
    async def session_status(request: Request) -> dict[str, object]:
        user = current_user(request)
        try:
            setup_required = await run_in_threadpool(accounts().count_users) == 0
        except AccountStoreUnavailable:
            raise ApiProblem(
                503,
                "service_unavailable",
                "The account database is temporarily unavailable.",
            ) from None
        return {
            "authenticated": user is not None,
            "setup_required": setup_required,
            "user": _user_json(user) if user else None,
        }

    @application.post("/api/setup", status_code=201)
    async def setup(request: Request, body: SetupRequest) -> JSONResponse:
        require_same_origin(request)
        if not setup_token_matches(body.setup_token):
            raise ApiProblem(403, "invalid_setup_token", "The setup token is not valid.")
        try:
            application.state.sessions.ensure_configured()
        except RuntimeError:
            raise ApiProblem(
                503,
                "service_unavailable",
                "The app is not ready for owner setup yet.",
            ) from None
        password = generate_password()
        password_hash = await run_in_threadpool(application.state.passwords.hash, password)
        try:
            user = await run_in_threadpool(
                accounts().create_owner,
                normalize_email(str(body.email)),
                password_hash,
            )
        except SetupAlreadyComplete:
            raise ApiProblem(409, "setup_complete", "Owner setup is already complete.") from None
        except EmailAlreadyExists:
            raise ApiProblem(409, "email_exists", "That email address is already in use.") from None
        except AccountStoreUnavailable:
            raise ApiProblem(
                503, "service_unavailable", "The account database is unavailable."
            ) from None
        response = JSONResponse(
            status_code=201,
            content={"user": _user_json(user), "password": password},
        )
        set_session_cookie(response, user)
        return response

    @application.post("/api/sessions")
    async def login(request: Request, body: LoginRequest) -> JSONResponse:
        require_same_origin(request)
        email = normalize_email(str(body.email))
        ip = request.client.host if request.client else "unknown"
        try:
            application.state.login_limits.check(email, ip)
        except LimitReached as error:
            raise ApiProblem(
                429,
                "login_rate_limited",
                error.message,
                headers={"Retry-After": str(error.retry_after)},
            ) from None
        try:
            user = await run_in_threadpool(accounts().get_user_by_email, email)
        except AccountStoreUnavailable:
            raise ApiProblem(
                503, "service_unavailable", "The account database is unavailable."
            ) from None
        valid = bool(
            user
            and user.enabled
            and await run_in_threadpool(
                application.state.passwords.verify, user.password_hash, body.password
            )
        )
        if not valid:
            application.state.login_limits.failure(email, ip)
            raise ApiProblem(401, "invalid_credentials", "The email or password is incorrect.")
        application.state.login_limits.success(email, ip)
        response = JSONResponse(content={"user": _user_json(user)})
        set_session_cookie(response, user)
        return response

    @application.delete("/api/session", status_code=204)
    async def logout(request: Request):
        require_same_origin(request)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=application.state.cookie_secure)
        return response

    @application.get("/api/users")
    async def list_users(request: Request) -> dict[str, object]:
        require_owner(request)
        try:
            users = await run_in_threadpool(accounts().list_users)
        except AccountStoreUnavailable:
            raise ApiProblem(
                503, "service_unavailable", "The account database is unavailable."
            ) from None
        return {"users": [_user_json(user) for user in users]}

    @application.post("/api/users", status_code=201)
    async def create_user(request: Request, body: CreateUserRequest) -> JSONResponse:
        require_same_origin(request)
        require_owner(request)
        password = generate_password()
        password_hash = await run_in_threadpool(application.state.passwords.hash, password)
        try:
            user = await run_in_threadpool(
                accounts().create_user, normalize_email(str(body.email)), password_hash
            )
        except EmailAlreadyExists:
            raise ApiProblem(409, "email_exists", "That email address is already in use.") from None
        except AccountLimitReached:
            raise ApiProblem(
                409, "account_limit", "Both user accounts are already active."
            ) from None
        except AccountStoreUnavailable:
            raise ApiProblem(
                503, "service_unavailable", "The account database is unavailable."
            ) from None
        return JSONResponse(
            status_code=201,
            content={"user": _user_json(user), "password": password},
        )

    @application.post("/api/users/{user_id}/password-resets")
    async def reset_user_password(request: Request, user_id: int) -> JSONResponse:
        require_same_origin(request)
        require_owner(request)
        password = generate_password()
        password_hash = await run_in_threadpool(application.state.passwords.hash, password)
        try:
            user = await run_in_threadpool(accounts().reset_password, user_id, password_hash)
        except AccountStoreUnavailable:
            raise ApiProblem(
                503, "service_unavailable", "The account database is unavailable."
            ) from None
        if user is None:
            raise ApiProblem(404, "user_not_found", "That user does not exist.")
        return JSONResponse(content={"user": _user_json(user), "password": password})

    @application.patch("/api/users/{user_id}")
    async def update_user(
        request: Request, user_id: int, body: UserUpdateRequest
    ) -> dict[str, object]:
        require_same_origin(request)
        owner = require_owner(request)
        if user_id == owner.id and not body.enabled:
            raise ApiProblem(409, "owner_required", "The owner account cannot be disabled.")
        try:
            user = await run_in_threadpool(accounts().set_enabled, user_id, body.enabled)
        except AccountLimitReached:
            raise ApiProblem(
                409, "account_limit", "Both user accounts are already active."
            ) from None
        except AccountStoreUnavailable:
            raise ApiProblem(
                503, "service_unavailable", "The account database is unavailable."
            ) from None
        if user is None:
            raise ApiProblem(404, "user_not_found", "That user does not exist.")
        return {"user": _user_json(user)}

    @application.get("/api/drafts", response_model=SavedDraftListResponse)
    async def list_saved_drafts(request: Request) -> dict[str, object]:
        user = require_user(request)
        drafts = await run_in_threadpool(application.state.ebay_store.list_drafts, user.id)
        listings = await run_in_threadpool(
            application.state.ebay_store.get_draft_listings,
            user.id,
            [draft.id for draft in drafts],
        )
        return {
            "drafts": [
                SavedDraftSummary(
                    id=(detail := saved_draft_json(draft, listings.get(draft.id))).id,
                    title=detail.draft.title,
                    photo_count=detail.photo_count,
                    updated_at=detail.updated_at,
                    status=detail.status,
                    ebay_item_id=detail.ebay_item_id,
                    listing_url=detail.listing_url,
                    seller_hub_url=detail.seller_hub_url,
                ).model_dump(mode="json")
                for draft in drafts
            ]
        }

    @application.get("/api/drafts/{draft_id}", response_model=SavedDraftResponse)
    async def get_saved_draft(request: Request, draft_id: str) -> SavedDraftResponse:
        user = require_user(request)
        saved = await run_in_threadpool(
            application.state.ebay_store.get_draft, user.id, valid_draft_id(draft_id)
        )
        if saved is None:
            raise ApiProblem(404, "draft_not_found", "That saved draft does not exist.")
        listing = await run_in_threadpool(
            application.state.ebay_store.get_listing, user.id, saved.id
        )
        return saved_draft_json(saved, listing)

    @application.put("/api/drafts/{draft_id}", response_model=SavedDraftResponse)
    async def save_listing_draft(
        request: Request, draft_id: str, body: SavedDraftUpdate
    ) -> SavedDraftResponse:
        require_same_origin(request)
        user = require_user(request)
        normalized_draft_id = valid_draft_id(draft_id)
        listing = await run_in_threadpool(
            application.state.ebay_store.get_listing, user.id, normalized_draft_id
        )
        if listing and listing.ebay_item_id:
            raise ApiProblem(
                409,
                "draft_published",
                "This draft is published and cannot be changed. "
                "Edit the live listing in Seller Hub.",
            )
        try:
            saved = await run_in_threadpool(
                application.state.ebay_store.save_draft,
                user.id,
                normalized_draft_id,
                body.draft.model_dump(mode="json"),
                body.ebay_category.model_dump(mode="json") if body.ebay_category else None,
                body.ebay_condition.model_dump(mode="json") if body.ebay_condition else None,
                body.photo_count,
                body.revision,
                True,
            )
        except DraftRevisionConflict:
            raise ApiProblem(
                409,
                "draft_changed",
                "This draft changed in another tab. Reload it before you continue.",
            ) from None
        except DraftPublicationInProgress:
            raise ApiProblem(
                409,
                "draft_publication_in_progress",
                "Publication is in progress. Wait for it to finish before you change this draft.",
            ) from None
        except EbayStoreUnavailable as error:
            _log_draft_store_failure("retry_save", normalized_draft_id, error)
            raise ApiProblem(
                503,
                "draft_save_unavailable",
                "We could not save this draft. Keep this page open and try Save now again.",
            ) from None
        if saved is None:
            raise ApiProblem(404, "draft_not_found", "That saved draft does not exist.")
        return saved_draft_json(saved)

    @application.delete("/api/drafts/{draft_id}", status_code=204)
    async def delete_listing_draft(request: Request, draft_id: str) -> Response:
        require_same_origin(request)
        user = require_user(request)
        try:
            deleted = await run_in_threadpool(
                application.state.ebay_store.delete_draft, user.id, valid_draft_id(draft_id)
            )
        except DraftPublicationInProgress:
            raise ApiProblem(
                409,
                "draft_publication_in_progress",
                "Publication is in progress. Wait for it to finish before you delete this draft.",
            ) from None
        if not deleted:
            raise ApiProblem(404, "draft_not_found", "That saved draft does not exist.")
        return Response(status_code=204)

    @application.post("/api/listings/generate", response_model=GenerationResponse)
    async def generate_listing(
        request: Request,
        photos: Annotated[list[UploadFile] | None, File()] = None,
        notes: Annotated[str, Form()] = "",
        ebay_category: Annotated[str, Form()] = "",
    ) -> GenerationResponse:
        uploads = photos or []
        try:
            require_same_origin(request)
            user = require_user(request)
            if not uploads:
                raise ApiProblem(400, "photos_required", "Choose at least one photo.")
            if len(uploads) > MAX_PHOTOS:
                raise ApiProblem(400, "too_many_photos", "Choose no more than 24 photos.")
            if len(notes) > MAX_NOTE_CHARACTERS:
                raise ApiProblem(400, "notes_too_long", "Keep seller notes under 2,000 characters.")

            try:
                category = DraftEbayCategory.model_validate_json(ebay_category)
            except ValidationError:
                raise ApiProblem(
                    422,
                    "invalid_category",
                    (
                        "Select an eBay category before generating. "
                        "Reload the page if the category control is missing."
                    ),
                ) from None
            try:
                requirements = await run_in_threadpool(
                    application.state.ebay_gateway.category_requirements, category.category_id
                )
                category_output_model(requirements)
            except EbayApiError:
                raise ApiProblem(
                    502,
                    "ebay_request_failed",
                    "Could not load the selected category. Search again or retry.",
                ) from None
            except CategorySchemaUnsupported:
                raise ApiProblem(
                    422,
                    "unsupported_category",
                    (
                        "This category has too many rules for generation. "
                        "Select another category or contact the owner."
                    ),
                ) from None

            with TemporaryDirectory(prefix="listing-assistant-") as temporary_directory:
                workdir = Path(temporary_directory)
                prepared: list[PreparedImage] = []
                image_warnings: list[ImageWarning] = []

                for photo_number, photo in enumerate(uploads, start=1):
                    photo.file.seek(0)
                    try:
                        image, warnings_for_image = await run_in_threadpool(
                            prepare_image,
                            photo.file,
                            workdir,
                            photo_number,
                        )
                    except ImageInputError as error:
                        raise ApiProblem(error.status_code, error.code, error.message) from None
                    prepared.append(image)
                    image_warnings.extend(warnings_for_image)

                try:
                    with application.state.generation_limits.start(user.id):
                        draft = await run_in_threadpool(
                            application.state.generator.generate,
                            prepared,
                            notes.strip(),
                            category,
                            requirements,
                        )
                except LimitReached as error:
                    raise ApiProblem(
                        429,
                        "generation_rate_limited",
                        error.message,
                        headers={"Retry-After": str(error.retry_after)},
                    ) from None
                except GenerationUnavailable:
                    raise ApiProblem(
                        503,
                        "generation_unavailable",
                        "Listing generation is temporarily unavailable. Please try again later.",
                    ) from None
                except GenerationFailed:
                    raise ApiProblem(
                        502,
                        "generation_failed",
                        (
                            "We could not generate this listing. Your photos are still selected; "
                            "please try again."
                        ),
                    ) from None

                draft = clean_generated_specifics(draft, requirements)
                draft = draft.model_copy(
                    update={"suggested_category": category.name[:160], "generation_required": False}
                )
                draft_id = str(uuid.uuid4())
                saved = True
                try:
                    stored = await run_in_threadpool(
                        application.state.ebay_store.save_draft,
                        user.id,
                        draft_id,
                        draft.model_dump(mode="json"),
                        category.model_dump(mode="json"),
                        None,
                        len(uploads),
                    )
                    saved = stored is not None
                except EbayStoreUnavailable as error:
                    _log_draft_store_failure("initial_save", draft_id, error)
                    saved = False
                else:
                    if stored is None:
                        DRAFT_STORE_LOGGER.warning(
                            "operation=initial_save draft_id=%s outcome=failure "
                            "error_class=state_conflict sqlstate=none",
                            draft_id,
                        )
                return GenerationResponse(
                    draft_id=draft_id,
                    saved=saved,
                    draft_revision=stored.revision if saved and stored else None,
                    draft=draft,
                    ebay_category=category,
                    warnings=image_warnings,
                )
        finally:
            for photo in uploads:
                await photo.close()

    install_ebay_routes(
        application,
        api_problem=ApiProblem,
        require_user=require_user,
        require_same_origin=require_same_origin,
    )

    return application


app = create_app()
