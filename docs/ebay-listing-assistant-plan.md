# eBay Listing Assistant product plan

- Status: Current product baseline
- Date: 2026-08-05

## 1. Product principle and scope

Ease of use is the first priority. Prefer fewer choices, less setup, shorter waits, and
clear recovery. Keep the product and implementation simple until daily use proves that
more is needed.

This is a small production application for two trusted people in one household who
resell on eBay, mostly but not exclusively clothing. It is not an MVP, pilot, test
program, enterprise system, or current SaaS effort. There are no planned focus groups,
customer interviews, or formal pilot exercises. Improve the workflow from direct daily
use and business needs.

No requirement from the retired Kafka project carries forward. Git history is only
historical context.

## 2. User outcome

An authenticated user opens the app in a phone or desktop browser, uploads photos of
one product, optionally adds facts the photos cannot show, and receives an editable
draft containing the information needed to create an eBay listing. The user does not
need ChatGPT, Codex, an OpenAI account, an OpenAI subscription, or an API key.

The product automates the photo-to-listing-information step and direct publication.
After review, a connected user can publish the complete listing through the Trading
API. The result is editable in Seller Hub. Direct publication stays disabled until the
Production acceptance check passes.

## 3. Core workflow

1. The user signs in with an email address and password.
2. The user selects or drags in 1–24 item photos, can see their order, and can choose
   the main photo.
3. The user may add a short note containing measurements, defects, model numbers, or
   other facts that photos cannot show.
4. The user searches for and explicitly selects an eBay category, then selects
   **Generate listing** once.
5. The page saves and shows an editable listing draft with pricing guidance.
6. The user reviews uncertainty and either copies the result or selects condition
   and any policy choices that are not automatic. The selected category can be changed
   during review, which rechecks the existing item specifics.
7. One action saves edits, uploads or reuses photos, verifies the listing, and
   publishes it live. A failed request keeps the draft for a safe retry.

No watched folder, marker file, command line, desktop installation, or separate OpenAI
setup is part of the user flow.

## 4. Requirements

### 4.1 Product and usability

- **PR-001:** Ease of use is the primary decision criterion.
- **PR-002:** A user must understand the main action without documentation.
- **PR-003:** The core workflow must work in current phone and desktop browsers.
- **PR-004:** Optional controls stay out of the main path until household use shows
  they save meaningful time.
- **PR-005:** Errors explain what the user can do next in plain language.
- **PR-006:** Do not add enterprise, public-SaaS, or formal research processes to solve
  hypothetical future needs.

### 4.2 Listing generation

- **FR-001:** Accept 1–24 photos directly from the user's device, matching eBay's
  current listing-photo count limit.
- **FR-001a:** Each source photo may be up to 12 MB. Stream the theoretical 288 MB
  maximum request instead of buffering it in application memory.
- **FR-001b:** Accept JPEG/JPG, PNG, GIF, TIFF, BMP, WEBP, HEIC, and AVIF. Inspect the
  real content and normalize it server-side to a non-animated JPEG before OpenAI.
- **FR-002:** Preview selected photos and allow removal, reordering, and selection of
  the first image as the main photo before generation.
- **FR-003:** Accept optional free-form notes for facts such as measurements, defects,
  model numbers, and included accessories.
- **FR-004:** Category selection is required before generation. The seller selects Men,
  Women, or Kids, then selects an eBay leaf category from the group menu. Store each
  successful group list locally and retrieve it from eBay only on a local miss. One action submits photos,
  notes, and the selected category. Category selection works without seller OAuth and
  when direct publication is disabled. A category lookup failure permits retry but
  does not start a model request or consume generation quota.
- **FR-005:** Return these common, editable listing fields:
  - title, limited to 80 characters;
  - the selected category (also stored in the legacy suggested-category field) and search terms;
  - condition and condition description;
  - item description;
  - item specifics;
  - quantity, defaulting to one;
  - recommended fixed-price or auction format;
  - observed flaws and seller disclosures;
  - missing facts requiring seller review; and
  - pricing guidance defined by **FR-008**.
- **FR-005a:** Load current rules for the selected category before the single model
  request. Constrain generated names to supported seller-set aspects and give the model
  allowed values, cardinality, formats, lengths, and dependencies. Return evidence-backed
  values as arrays. Prioritize required and recommended facts. Omit invalid generated
  specifics, recheck dependencies after omissions, and identify missing required facts.
  Keep the usable incomplete draft; do not automatically make a repair request.
  Category changes after generation preserve seller data but require generation again.
  Save that requirement with the selected category and group, and block publication
  until the seller explicitly generates a new draft. A failed generation keeps the
  requirement; changing back to the original category does not clear it.
- **FR-005b:** Shipping measurements, location, policy choices, and identifiers not
  visible in the evidence remain editable unknowns rather than model inventions. The
  seller must enter package weight in whole pounds and ounces and package length,
  width, and height in whole inches before publication.
- **FR-006:** Results are readable, editable, and copyable without another app.
- **FR-007:** A failed generation can be retried without reselecting photos while the
  page remains open.
- **FR-007a:** Save each successful generated draft and later edits. Let its owner
  reopen or delete it without another OpenAI request.
- **FR-007b:** Keep ordered original photos with the draft in the originating browser's
  IndexedDB. Do not synchronize photo bytes between browsers. Keep published drafts
  read-only until the user deletes them.
- **FR-008:** The LLM returns a suggested listing price, expected sale-price range,
  confidence, short rationale, and recommended listing format in the same structured
  response. The UI labels pricing as editable guidance, not a live market quote.

### 4.3 Accounts and access

- **AR-001:** There are at most two active accounts. There is no public signup.
- **AR-002:** Each account has a unique normalized email address and password.
- **AR-003:** The owner creates a user by entering an email address. The server creates
  a cryptographically random 20-character password, stores only its Argon2id hash, and
  shows the plaintext once with a copy button.
- **AR-004:** The owner can disable an account or reset its password. Resetting creates
  and displays a new random password and immediately invalidates the old one.
- **AR-005:** Bootstrap the first owner through a one-time setup page protected by an
  encrypted runtime `APP_SETUP_TOKEN`. Disable setup once any user exists and remove
  the environment variable after bootstrap.
- **AR-006:** Use signed 30-day session cookies containing only user ID and password
  version. Set `Secure`, `HttpOnly`, and `SameSite=Lax`; sign with a separate encrypted
  runtime `SESSION_SECRET`; verify the password version on authenticated requests; and
  require same-origin protection for mutations.
- **AR-007:** Do not add email delivery, verification, self-service reset, MFA, OAuth,
  organizations, teams, or audit infrastructure until expansion requires it.

### 4.4 OpenAI access and secrets

- **SR-001:** All model operations use one operator-owned API key. Its only source is
  the server's `OPENAI_API_KEY` runtime environment variable, read only when the
  request path needs to call OpenAI.
- **SR-002:** OpenAI requests originate only from the server.
- **SR-003:** The key never appears in browser code, responses, logs, committed files,
  user-visible errors, metrics, traces, tests, or generated artifacts.
- **SR-004:** Normal automated tests never make paid OpenAI calls. Live tests are
  explicit and opt-in.
- **SR-005:** A missing key fails closed with a safe message.
- **SR-006:** Use a dedicated OpenAI project with an $8/month hard spend limit while
  the web service and account database cost $12/month. Configure earlier alerts.

### 4.5 Privacy and storage

- **TR-000:** Publish a public privacy policy that states what the app collects, how it
  uses and retains the information, which service providers process it, that personal
  information is not sold, and how to contact the installation's operator. Read the
  public operator name and contact email from runtime settings, not source code.
- **TR-001:** Tell users that OpenAI analyzes the photos to generate the draft.
- **TR-002:** Validate file type, count, and size before the OpenAI request.
- **TR-002a:** Warn when a photo is below eBay's 500-pixel minimum on its longest side
  and recommend a larger image without blocking an otherwise valid upload.
- **TR-003:** Distinguish photo-derived observations from uncertain inference. Never
  silently invent condition, authenticity, measurements, or included items.
- **TR-004:** Keep server-side source and normalized photos only in request-scoped
  temporary storage and delete them on success or failure. The originating browser can
  keep original photo bytes under **TR-006**.
- **TR-005:** Persist unfinished generated drafts, eBay-ready listing snapshots,
  mappings, historical handoff state, and eBay media references needed for recovery.
  Keep a confirmed published source draft as a read-only record until the user deletes
  it. Do not persist source or normalized photo bytes on the server.
- **TR-006:** Store ordered original photos in IndexedDB for the originating browser
  profile and site. Apply one 1 GiB browser limit, warn above 75% and at 900 MiB, and
  block a new generation that would exceed 1 GiB. Show current-draft and total-browser
  use. Re-upload local photos for eBay transfer; other browsers require reselection.

### 4.7 eBay connection, drafts, and live-listing safety

- **ER-001:** Each app user can connect one different eBay seller account through
  OAuth. Store the immutable eBay user ID and an encrypted refresh token. Support
  status, disconnect, expired authorization, and reconnect.
- **ER-002:** Support `EBAY_US`, USD, fixed-price listings, quantity one, and no
  variations in the first release.
- **ER-003:** Keep an API-neutral eBay-ready listing model. Use Taxonomy and Metadata
  APIs for category suggestions, conditions, and item-specific requirements.
- **ER-004:** Assign a stable app custom label to each prepared listing.
- **ER-005:** Upload 1–24 ordered, metadata-free photos through the Media API. Store
  only image IDs, URLs, positions, and expiry times.
- **ER-006:** The active `Publish live on eBay` action uses the saved draft as its
  source. It saves edits, reuses valid Media API references, verifies through Trading,
  and publishes the complete listing. New CSV and Feed handoffs are retired.
- **ER-006a:** Keep authenticated status and CSV download routes for historical
  transfers. A ready or failed old handoff requires confirmation that no Seller Hub
  draft exists. A completed transfer blocks direct publication.
- **ER-007:** Put all live-listing XML inside one Trading API adapter. Support verify,
  publish, retrieve, revise, end, and relist. Use modern REST APIs for supporting data.
- **ER-008:** Keep direct publication disabled by default until external acceptance.
  Verify before every add, require existing business policies and a shared item postal
  code, require positive package weight and dimensions, validate Taxonomy aspect
  rules, and use the saved draft UUID for retry safety.
- **ER-008a:** Save each successful photo upload immediately. On an unconfirmed or
  failed publication, keep the unfinished draft, listing snapshot, policy IDs, and
  unexpired media references. Return HTTP success only with a confirmed eBay item ID.
- **ER-008b:** After confirmed publication, mark the source draft published and
  read-only. Delete it only on user request. Draft deletion removes browser photos but
  preserves the live eBay mapping and never changes the eBay listing.
- **ER-009:** Fetch the current eBay listing before revision. If it changed since the
  app loaded it, require refresh. Preserve fields that the app does not edit.
- **ER-010:** Implement eBay's marketplace account-deletion challenge and signed
  notification handling before any Production API call.
- **ER-011:** Treat eBay as the source of truth for live listing state. Keep orders,
  shipping, returns, messages, and payouts in Seller Hub.

### 4.6 Load and abuse protection

- **LR-001:** Allow 24 generation starts per rolling hour per account and 30 per
  rolling day per account. Count only authenticated requests that pass input
  validation and start generation.
- **LR-002:** Allow one in-flight generation per account and two globally.
- **LR-003:** Limit failed logins to 10 per 15 minutes for an email-and-IP pair and 30
  per hour per IP.
- **LR-004:** In-memory counters are sufficient while the app is one process. A reset
  during deployment is acceptable for these trusted users.
- **LR-005:** Treat the OpenAI project hard limit—not request-rate limits—as the cost
  backstop.

The stretch load is 10 products in one day, sometimes concentrated into one hour.
That uses 41.7% of the hourly generation allowance (`10 / 24`) and 33.3% of the daily
allowance (`10 / 30`), satisfying the requirement to remain below 50%.

## 5. Application design

```text
phone or desktop browser
          |
          | login, then photos + optional notes
          v
single FastAPI web service  <---->  small PostgreSQL account + eBay mapping store
  - serves the pages
  - authenticates two users
  - validates and temporarily normalizes uploads
  - calls OpenAI with a server secret
  - validates structured output
  - saves unfinished listing drafts for recovery
  - connects each user to eBay with OAuth
  - uploads or reuses photos and publishes through a gated Trading API adapter
          |
          v
editable listing draft in the browser  <---->  eBay support APIs and Seller Hub
```

Use the stack fixed by [decision 0004](decisions/0004-python-fastapi-web-stack.md):
Python 3.13 with `uv`, FastAPI/Uvicorn, a static HTML/CSS/vanilla JavaScript client,
Pydantic, Pillow plus `pillow-heif`, pytest, Playwright, and Ruff. Use DigitalOcean's
Python buildpack with one Uvicorn worker. Do not add Kafka, a queue, a separate worker,
object storage, a frontend build pipeline, or a desktop wrapper.

The PostgreSQL database is the one narrow exception to the otherwise stateless design.
It exists because App Platform containers and their SQLite files are ephemeral. It
stores accounts, unfinished listing drafts, encrypted eBay authorization, listing
mappings, eBay-ready snapshots, Media API references, handoff state, and historical
Feed task state. It never stores photo bytes.

Use `psycopg` and small versioned SQL migration files, not an ORM or migration
framework. Use `argon2-cffi` for password hashes and `itsdangerous` for signed cookies.
Increment a user's password version on reset so existing cookies stop working without
adding a server-side session table.

## 6. Model and image baseline

The known-good terminal workflow uses `gpt-5.5` and medium-quality JPEG exports. That
is the quality bar and the application's starting baseline:

- default to `gpt-5.5`, still overridable with non-secret `OPENAI_MODEL`;
- normalize every input to metadata-free JPEG at quality 75;
- cap the longest edge at 2048 pixels;
- use `high` image detail; and
- make one structured Responses API call for listing fields and pricing.

Do not upgrade image fidelity or choose a more expensive model merely because one is
available. Do not switch to a cheaper model merely because it costs less. Compare any
candidate against representative apparel and non-apparel photos from the working
terminal flow. Change the default only if it meets or exceeds the existing useful
output while improving cost, latency, or a concrete business result.

At current published GPT-5.5 standard pricing, input is $5 per million tokens and
output is $30 per million tokens. With `high` detail, GPT-5.5 uses at most 2,500 image
patch tokens per image; the 2048-pixel cap also bounds payload size. A 24-photo request
can therefore spend up to about $0.30 on image input alone, before text, output, and
reasoning. Ten products every day would not fit the $8 monthly API allocation. That
load is a capacity target, not a spending promise; measure actual per-listing cost and
let the project hard limit stop further spend.

## 7. Deployment and budget

Deploy one web service to DigitalOcean App Platform on
`apps-s-1vcpu-0.5gb`: one shared CPU, 512 MiB RAM, and a current price of $5/month.
Add its $7/month, 512 MB development PostgreSQL database for account persistence. Use
the platform URL and managed HTTPS.

| Item | Monthly limit |
|---|---:|
| DigitalOcean web service | $5 |
| DigitalOcean development PostgreSQL | $7 |
| OpenAI project hard spend limit | $8 |
| Total | $20 |

The development database has no built-in backup and is not DigitalOcean's
high-availability managed database. This is an accepted low-cost tradeoff for two
trusted household users. A database loss can require account recreation, eBay
reconnection, and manual recreation of unfinished work. Do not pay for object storage
or another service. If memory requires a larger web tier, revisit the full budget
before upgrading rather than silently reducing the OpenAI allocation below a useful
level.

## 8. Not in scope now

- Inventory API listings, variations, auctions, and multi-quantity listings.
- Categories that require variations, regulatory documents, or special condition
  descriptors.
- Existing-listing import, inventory sync, orders, shipping, returns, messages, or
  payouts.
- Server-side source-photo storage or completed-listing history beyond retained
  app-created drafts.
- A desktop installer or native mobile app.
- Public signup, more than two active users, teams, billing, subscriptions, or SaaS
  administration.
- Focus groups, a pilot program, customer research, enterprise security programs, or
  formal product analytics.
- Bulk processing, background jobs, queues, or distributed orchestration.
- Live comparable-sale retrieval or claims that the LLM price is a market quote.

## 9. Operational acceptance

The current workflow is ready for regular household use when:

- **OA-001:** The owner can complete one-time setup, create the second user by email,
  and copy the
  generated password;
- **OA-002:** Both users can sign in on phone and desktop, and unauthenticated access
  is blocked;
- **OA-003:** A user can choose photos, generate a draft, edit it, and copy it without
  an OpenAI
  credential or special instructions;
- **OA-004:** The app accepts 1–24 supported photos at no more than 12 MB each without
  buffering a maximum request in memory;
- **OA-005:** The app normalizes HEIC and other unsupported OpenAI formats to
  medium-quality JPEG and deletes all temporary images;
- **OA-006:** The output meets the existing GPT-5.5 terminal workflow's useful
  quality;
- **OA-007:** Generation and login limits behave as specified and recover with clear
  messages;
- **OA-008:** Secrets are server-only, and failure paths do not expose them or lose
  the in-page photo selection;
- **OA-009:** One action can publish every reviewed eBay field and ordered photo,
  failures can be retried safely, and the live listing remains editable in Seller
  Hub;
- **OA-009a:** The originating browser restores ordered draft photos, enforces the
  1 GiB limit before generation, and retains a read-only published draft until user
  deletion;
- **OA-010:** Hosting, database, and configured OpenAI hard limits total no more than
  $20/month; and
- **OA-011:** The deployed service remains usable under the defined two-user
  concurrency and maximum-request capacity checks.

## 10. Delivery plan and traceability

### 10.1 Milestones and dependencies

The logical dependency order is:

`M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9`

| Milestone | Bounded work package | Status | Coherent result |
|---|---|---|---|
| M1 | WP-01 Photo-to-draft | Complete | A user gets an editable, pricing-aware draft from validated photos. |
| M2 | WP-02 Access and runtime safety | Complete | Two accounts can use one low-cost service without exposing secrets or exceeding defined load limits. |
| M3 | WP-03 Draft recovery | Complete | A user can save, reopen, edit, and delete an unfinished draft without another model request. |
| M4 | WP-04 eBay connection and support data | Complete | Each user can connect one seller account, and the app can get the required eBay data safely. |
| M5 | WP-05 Retry-safe Trading adapter | Complete | The server can prepare, verify, publish, and recover one complete supported listing. |
| M6 | WP-06 Browser publication path | Complete | The browser has one visible publication path with clear gates and safe retry behavior. |
| M7 | WP-07 Production readiness | Current | External eBay behavior and the deployment capacity gate have recorded evidence. |
| M8 | WP-08 Household enablement | Next, provisional | Direct publication is enabled and one normal listing succeeds for each account. |
| M9 | WP-09 Possible listing management | Later, conditional | Existing-listing work starts only after reliable new-listing use proves a need. |

M7 is the only fully planned open milestone. M8 can change after M7 evidence. M9 stays
at acceptance-criteria level until household use justifies it.

### 10.2 Requirement traceability

| Acceptance criteria | Milestone and work package | Verification path |
|---|---|---|
| PR-001, PR-002, PR-003, PR-004, PR-005, PR-006 | M1/WP-01 and M6/WP-06 | `tests/browser/test_listing_flow.py` plus the M7 phone and desktop clarity check |
| FR-001, FR-001a, FR-001b, FR-002, FR-003, FR-004, FR-005, FR-005a, FR-005b, FR-006, FR-007, FR-008 | M1/WP-01 | `tests/test_main.py`, `tests/test_images.py`, `tests/test_models.py`, `tests/test_generation.py`, and the browser suite |
| FR-007a, FR-007b | M3/WP-03 | Saved-draft route tests in `tests/test_main.py` and reopen tests in the browser suite |
| AR-001, AR-002, AR-003, AR-004, AR-005, AR-006, AR-007 | M2/WP-02 | `tests/test_auth.py` and the owner-create-user browser test |
| SR-001, SR-002, SR-003, SR-004, SR-005, SR-006 | M1/WP-01, M2/WP-02, and M7/WP-07 | Generation, route, access-log, and secret-exposure tests plus the Production budget check |
| TR-000, TR-001, TR-002, TR-002a, TR-003, TR-004 | M1/WP-01 and M4/WP-04 | Privacy, upload, image-inspection, uncertainty, and deletion tests |
| TR-005, TR-006 | M3/WP-03, M5/WP-05, and M6/WP-06 | Draft, browser photo storage, Media reference, partial-upload, publication, and retry tests |
| LR-001, LR-002, LR-003, LR-004, LR-005 | M2/WP-02 and M7/WP-07 | `tests/test_limits.py`, authentication limit tests, budget check, and the M7 capacity check |
| ER-001, ER-002, ER-003, ER-004, ER-005, ER-010 | M4/WP-04 | eBay route, API, security, Taxonomy, Media, and deletion-notification tests |
| ER-006, ER-006a, ER-007, ER-008, ER-008a, ER-008b, and ER-011 | M5/WP-05, M6/WP-06, and M7/WP-07 | Trading XML, route gate, recovery, phone retry, and external Seller Hub checks |
| ER-009 | M5/WP-05 | Retrieve, conflict, preserve-field, revise, end, and relist adapter tests |
| OA-001, OA-002 | M2/WP-02 and M7/WP-07 | Authentication tests plus current phone and desktop checks for both accounts |
| OA-003, OA-004, OA-005, OA-006, OA-007, OA-008 | M1/WP-01, M2/WP-02, M3/WP-03, and M7/WP-07 | Locked test suite, browser suite, representative quality comparison, and capacity evidence |
| OA-009, OA-009a | M3/WP-03, M5/WP-05, M6/WP-06, and M7/WP-07 | Locked draft-photo, Trading, and browser tests plus the low-risk Production listing and Seller Hub edit check |
| OA-010, OA-011 | M2/WP-02 and M7/WP-07 | Current platform configuration, spend limit, and the named capacity check below |

`bin/validate-repo` is the common correctness gate for every code milestone. Run the
browser suite for a changed browser flow. Live OpenAI and eBay checks stay explicit and
do not run in normal validation.

### 10.3 WP-07 Production readiness: current work package

- **Goal:** Prove that the implemented supported listing path works in Production and
  that the service stays within its accepted capacity and cost boundaries.
- **Context:** Deterministic tests cover contracts and failure paths, but they cannot
  prove Seller Hub behavior, deployed image support, model quality, or peak memory.
- **Dependencies:** M1–M6, valid Production credentials and policies, the shared postal
  code, two active app accounts, and the direct-publication runtime override.
- **Required behavior:** Run the low-risk listing and Seller Hub edit check from
  decision 0006. Confirm the earlier setup, login, generation, recovery, image, secret,
  quality, limit, and budget evidence. Run the capacity check below. Record each result
  while M7 is current.
- **Constraints:** Use non-private test data where possible. Do not record credentials,
  tokens, private photos, or private listing data. Keep the one-service architecture,
  two-user limit, request-scoped server photo lifetime, device-local draft photo rules,
  GPT-5.5 baseline, and $20 monthly limit.
- **Acceptance criteria:** OA-001–OA-011 pass. The low-risk listing contains every
  expected field and ordered photo, a Seller Hub edit remains, no request loses
  recovery data, and all capacity limits below pass. Any failure keeps direct
  publication disabled in the committed baseline.
- **Affected invariants:** Ease of use, server-only secrets, private temporary server
  photos, device-local draft photos, retry safety, Seller Hub as source of truth, one
  deployable service, and the monthly budget.
- **Required checks:** `bin/validate-repo`; the Playwright browser suite if the flow
  changes; the external eBay check; the named capacity check; and a separate review of
  the result and evidence before the default flag changes.
- **Out of scope:** A model change, an image-fidelity change, listing import, live
  listing management UI, variations, a queue, another service, or an eBay API change.

The named performance gate is **two-user maximum-request capacity**:

- **Dataset:** A fixed, checksum-recorded, non-private set of 24 valid files per
  request at the 12 MB boundary. The combined set must include every supported source
  format. Use the same set for each run.
- **Load and hardware:** Run one-user and two-user cases against one Uvicorn worker in
  the 512 MiB App Platform service or a verified equivalent memory limit. Use enough
  repeat runs to report p50 and p95 elapsed time.
- **Memory and allocation limits:** Peak service memory must stay below 512 MiB, with
  no process restart, out-of-memory event, whole-request memory buffer, or temporary
  file left after success or failure.
- **Latency limit:** Unverified. Record p50 and p95 first. The owner must accept a
  numeric usability limit from representative measurements before OA-011 can pass. Do
  not claim a latency result from one run or from model-provider timing alone.
- **Cost limit:** The check must not cause the configured monthly total to exceed $20.
  Use deterministic fake model responses for repeat capacity runs. Keep any live model
  quality and cost sample explicit and bounded.

### 10.4 Later rolling-wave milestones

M8 is provisional. Its acceptance criteria are a committed
`EBAY_DIRECT_PUBLISH_ENABLED=true` baseline, one normal successful listing for each
household account, correct Seller Hub records, and no regression in OA-001–OA-011.
Define its exact work package only after M7 passes.

M9 starts only if reliable household use shows that importing or managing existing
fixed-price listings saves meaningful work. Before implementation, create an accepted
decision with the supported operations, source-of-truth rules, conflict behavior,
recovery behavior, acceptance criteria, and tests. Otherwise, keep this milestone
closed.

## 11. Remaining decisions

- Does GPT-5.6 Terra or another lower-cost model match the known GPT-5.5 output on the
  household's representative products?
- Is the $8 OpenAI allocation useful at actual photo counts and output-token usage?
- Whether direct Trading publication has passed its Production acceptance checks and
  can be enabled for normal use.
- Whether reliable new-listing use justifies importing existing fixed-price listings.

## 12. Current external baselines

Recheck these changing facts before deployment or a pricing-sensitive change:

- [eBay photo guidance](https://www.ebay.com/help/selling/listings/adding-pictures-listings?id=4148)
- [eBay common item specifics](https://www.ebay.com/sellercenter/listings/item-specifics)
- [eBay 80-character title limit](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/listing-title.html)
- [eBay listing management guide](https://developer.ebay.com/develop/selling-applications/listing-management)
- [eBay marketplace account deletion](https://developer.ebay.com/develop/guides-v2/marketplace-user-account-deletion)
- [eBay API deprecation status](https://developer.ebay.com/develop/get-started/api-deprecation-status)
- [OpenAI GPT-5.5 model](https://developers.openai.com/api/docs/models/gpt-5.5)
- [OpenAI image inputs and tokenization](https://developers.openai.com/api/docs/guides/images-vision)
- [DigitalOcean App Platform pricing](https://docs.digitalocean.com/products/app-platform/details/pricing/)
- [DigitalOcean App Platform storage](https://docs.digitalocean.com/products/app-platform/how-to/store-data/)
- [DigitalOcean App Platform databases](https://docs.digitalocean.com/products/app-platform/how-to/manage-databases/)
