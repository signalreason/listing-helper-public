# Project state

Updated: 2026-09-08

## Current objective

Complete M7 Production readiness: finish the external Production acceptance check for
direct Trading publication and record the deployment capacity evidence. Then enable
direct publication for normal household use.

## Implementation state

- Public-source preparation adds reseller setup and operating guides, a deployment
  template without operator details, and source/privacy checks. The privacy page reads
  `APP_OPERATOR_NAME` and `APP_PRIVACY_EMAIL` from runtime settings; configure both before
  deploying this change to an existing installation. GitHub Actions uses fake providers
  and scans for credentials without verification or raw finding output. Repository
  publication follows the owner-approved clean-snapshot procedure in
  `docs/public-release.md`, with an MIT license and the original repository kept private.
  Production acceptance remains incomplete.
- Local clean-database setup passed with PostgreSQL 16, including repeat migrations,
  health, policy contact, owner creation, and authenticated access. Hosted setup on the
  PostgreSQL 17 deployment template remains unverified. GitHub rejected secret-scanning
  enablement and private reporting on the original private repository. Branch protection
  there requires a plan upgrade. Configure these controls on the new public repository;
  the CI scanner is independent. The existing service has both privacy settings from its public policy;
  their deployment is active and health and privacy endpoints return HTTP 200.

- The browser shows Men, Women, and Kids group menus, then each group's eBay leaf
  categories. PostgreSQL stores successful lists by environment, US tree, and group;
  only a local miss calls eBay. Failed loads remain retryable. Saved drafts restore
  both menus and ignore late responses from an earlier group or draft. Legacy drafts
  retain their category while the app checks actual group membership. HTML and static
  assets disable browser caching; the category release uses new asset addresses to
  bypass previously cached scripts.
- Generation accepts 1–24 photos and normalizes them sequentially. The server loads
  current category rules before one model request, limits generated names, omits
  invalid and unknown-source values, rechecks dependencies, and identifies missing
  required facts. The initial save and response retain the category and group.
- A category change preserves the old fields but marks the draft as requiring
  generation. The browser and server block publication. This state survives saves,
  reopening, failure, and return to the original category. Only successful explicit
  generation creates a new valid draft. Protected publication recovery keeps its
  immutable snapshot and permits separate new generation. Early photo selection is
  retained even when it precedes module loading.
- Two email/password accounts, signed sessions, load limits, same-origin protection,
  PostgreSQL persistence, and the DigitalOcean deployment baseline remain active.
- Each user can connect one different eBay seller account through OAuth. Encrypted
  refresh-token storage, reconnect, account-deletion compliance, and the public privacy
  page remain active.
- The normal eBay action is `Publish live on eBay`. It reads a saved draft on the
  server, loads the seller's existing shipping, payment, and return policies, applies
  the selected category and condition, and uses the shared encrypted
  `EBAY_ITEM_POSTAL_CODE` runtime value.
- Taxonomy requirements retain required and recommended status, mode, cardinality,
  maximum length, type, format, applicability, allowed values, and conditional value
  dependencies. Item specifics use value arrays. The browser provides category-driven
  fields and immediate errors, and the server reloads current rules and blocks every
  invalid or unsupported rule before photo transfer. Legacy single-value drafts convert
  when they load. Because this app does not attach an eBay catalog product, it accepts
  and sends reviewed `ITEM`, `PRODUCT`, and combined-applicability aspects. Unknown
  applicability values remain blocked. The browser reconciles untouched generated
  specifics on the initial category load. Later category changes preserve seller data. A
  missing required aspect appears as one blank, non-removable field with one instruction;
  the app does not invent its value. The server still rejects unknown names and missing
  required values from stale or modified requests.
- Category suggestions and requirements reuse an expiry-aware eBay application token.
  A lock permits one refresh at the safety boundary, and a cached-token HTTP 401 causes
  one token refresh and one retry without changing seller authorization state.
- The originating browser keeps ordered original draft photos in IndexedDB until user
  deletion. It shows current and total use, applies a 1 GiB browser-profile limit, and
  prevents cross-account photo access. Local photo sets are bound to monotonic server
  draft revisions. Draft PUT requests must match the loaded revision, so a stale tab
  cannot overwrite newer server draft data or bind old photos to it. A null revision
  can create only a missing draft after an initial generation-save failure.
  Text-only autosaves advance only local revision metadata and do not rewrite photo
  bytes. A failed photo write makes the save incomplete and blocks saved-draft navigation
  that would discard an in-memory photo edit. A partial re-selection of a missing original
  photo set also blocks draft saves and saved-draft navigation. Neither case blocks a new
  generation, which creates a separate draft while the prior draft stays at its last saved
  revision. A clean reopened draft with no local photo copy can still navigate away. If a
  photo changes during an in-flight save, the incomplete result still blocks its caller
  while a new save persists the newer local revision. One shared operation owns the
  tombstone read and PUT, so concurrent save callers cannot send the same loaded revision.
  Publication stops if the current photos cannot be bound to the exact saved
  revision. A durable account-scoped intent and IndexedDB tombstone precede server
  deletion. The open draft detaches and freezes before the tombstone wait, so no late
  autosave or dropped photo can change it. A definite deletion rejection restores the
  draft and requeues an autosave that deletion canceled. An interrupted request can retry both server and local photo cleanup,
  while delayed cross-tab writes cannot recreate deleted photos. Owner tokens prevent
  one failed deletion from reversing another tab's tombstone or removing its pending
  cleanup intent. Each saved-draft refresh reloads tombstones from IndexedDB, so a
  cross-tab cleanup intent cannot leave the server draft editable. Each draft PUT also
  rechecks IndexedDB, blocks on a read failure, and closes the editor if a new tombstone
  exists. Saved-draft opening checks before its GET and again before rendering, so a
  new cross-tab tombstone cannot expose an editor. One open operation owns the complete
  transition through the target render and final storage display read. It freezes old
  and newly rendered controls, invalidates earlier category and policy loaders, and
  defers late connection-driven loaders until the target is ready. Photo additions
  recheck all transition locks after asynchronous capacity reads. Versioned draft-list
  and storage-display refreshes ignore older results. Saved-draft navigation can skip
  the current-draft tombstone read only for a clean no-write draft when IndexedDB is
  unavailable; publication and any path that can PUT or write photos still require the
  deletion check. Server draft deletion remains available when IndexedDB is unavailable, with a
  warning about possible local site data. Server-only generation and deletion also remain
  available if an open IndexedDB handle becomes unusable. The saved-draft list then keeps
  showing server records without another local tombstone read. A first failure during
  tombstone refresh also falls back to the already-fetched server list. Existing draft PUTs remain
  blocked when IndexedDB did not open because an unreadable tombstone can exist. A new
  server-only generation can still create a draft. Server photo bytes remain request-scoped.
- Direct preparation saves its listing snapshot and source revision atomically, uploads
  photos one at a time through the Media API, and binds each saved reference to that
  revision. A retry skips each unexpired reference. A stale recovery lease can refresh
  expired references from the original browser photos. A new source revision discards
  the old references, including references from a retained historical transfer, before
  it uploads the current ordered set. Starting generation invalidates earlier category
  loaders so they cannot mutate or save into its result.
- Publication always calls `VerifyAddFixedPriceItem` before `AddFixedPriceItem`. The
  Trading XML includes title, category, condition, condition description, description,
  price, quantity one, structured item specifics, 1–24 ordered photo URLs, all three
  business policies, package weight in pounds and ounces, package length, width, and
  height in whole inches, SKU, and postal code. Package measurements are seller-entered
  and saved with the unfinished draft.
- A stale browser page that omits required package dimensions gets a safe reload
  instruction. Current forms list every reason that publication is blocked, mark
  invalid controls, and round positive decimal dimensions up to saved whole inches.
  Disabled publish actions use a blocked cursor rather than a loading cursor.
- The saved draft UUID is reused for every add attempt. Duplicate-UUID responses can
  recover the original eBay item ID. Network failures and unacknowledged responses
  return a retry error and do not remove recovery data or return HTTP 200.
- A confirmed publication saves the eBay item ID, marks the listing live, retains the
  source draft and browser photos, and makes the draft read-only. User deletion removes
  the draft and local photos but preserves the live mapping. Seller Hub is the source
  of truth after publication. Publication rechecks the source revision and blocks
  concurrent draft saves and deletion from the first eBay call through the live-state
  commit. A 15-minute lease lets an interrupted publication resume its unchanged
  preparation and idempotent eBay request after a process restart. Lease fencing prevents
  an older request from starting an add, clearing, or completing a renewed lease. The protected listing
  snapshot and photo URLs load after lease acquisition, and expiry is checked again
  before any eBay call. The caller sends the exact revision and unique preparation ID, so
  publication, lease acquisition, and media writes reject same-revision replacement by
  another tab. Legacy unversioned preparations and bodyless publish requests are rejected. A
  stale recovery preparation must identify the protected preparation, so an unprepared
  clean tab cannot publish policy choices that differ from its display. A listing with an
  eBay item ID rejects later preparation updates, so a stale tab cannot replace its live
  snapshot. A
  Fresh verification transport failure uses a non-recovery code because no add request
  started. The same failure during uncertain recovery uses a protected recovery code.
  Lost publish responses and unknown post-request failures default to protected recovery;
  protected verification and add rejections also have distinct recovery codes.
  A matching stale preparation resumes its stored snapshot before mutable current
  package, category, or policy validation. The browser can resume when the
  protected draft rejects its normal save, but it refuses recovery if the page has
  unsaved changes. A reopened protected draft exposes its recovery state, keeps policy
  selectors frozen at the prepared policy IDs, including a saved option for a policy
  that is no longer current, and restores its saved category and condition without
  mutable category, requirement, or policy loaders. Current metadata availability cannot
  block the protected retry. A delayed eBay connection load uses the same retained values.
  A transient preparation failure also retains the protected recovery identity. A failed
  retry save reapplies the protected business-policy lock.
  Transactional IndexedDB tombstones keep cleanup intents from different tabs without a
  shared whole-key local-storage update. Retryable deletion failures keep their cleanup
  tombstone and remove that draft from every editable saved-draft view until startup cleanup.
  A definite rejection clears this attempt's owner-matched tombstone only when this
  attempt wrote it. A definite startup-retry rejection does the same. Protected
  expired-photo responses also keep policy selectors frozen.
  Persisted uncertainty protects a renewed lease even if its first
  protected reload fails. Uncertain eBay or database outcomes retain the lease; only
  definite rejection clears it early.
- New CSV and Feed handoffs are retired. Authenticated status and CSV download routes
  remain for historical records. A ready or failed old handoff needs one confirmation
  that no Seller Hub draft exists; a completed transfer remains blocked.
- Existing retrieve, revise, end, and relist adapter methods remain unwired from the
  browser for possible later listing management. No queue, worker, import, polling,
  server photo store, or two-way sync was added.
- Deterministic route, Taxonomy, Trading XML, failure, partial-photo, secret, and phone
  retry tests use fake eBay responses and never create eBay objects.
- A failed automatic draft save keeps the generated data in the open page, shows Save
  now and copy-backup instructions, and permits a retry with the same draft UUID. The
  publication section and its disabled action stay visible while the eBay connection is
  loading or unavailable. The platform health check now covers the listing tables, and
  draft-store failures log only a safe failure class, SQL state, operation, and draft ID.

## Active constraints

- `EBAY_DIRECT_PUBLISH_ENABLED` stays `false` in the committed deployment baseline
  until the external Production check passes. The current Production runtime override
  is `true` so the household can complete the controlled listing check.
- The first supported shape is `EBAY_US`, USD, fixed price, quantity one, no variations,
  and no special regulatory documents or special condition descriptors.
- The two household sellers use one item postal code.
- One browser profile per app account is supported. Draft photos do not synchronize
  between browsers and browser storage is not a backup.
- Seller Hub owns live-listing edits, orders, shipping, returns, messages, and payouts.
- The existing $5 web service, $7 PostgreSQL database, and $8 OpenAI hard limit keep
  the monthly total at $20.

## Immediate next actions

1. After the current Production process has run for more than two hours, use an
   authenticated session to confirm that category suggestions and category
   requirements both succeed.
2. Use the visible direct-publication controls to publish one low-risk Production
   listing. The publish action runs `VerifyAddFixedPriceItem` before publication.
3. Confirm every field and ordered photo in
   Seller Hub, edit one field there, and confirm that the edit remains. End the listing
   if it exists only for validation.
4. Run the two-user maximum-request capacity check defined in section 10.3 of the
   product plan. Record peak memory, p50 and p95 elapsed time, restarts, temporary-file
   cleanup, and the accepted numeric latency limit without using private photos.
5. After the external and capacity checks pass, set the committed deployment baseline to
   `EBAY_DIRECT_PUBLISH_ENABLED=true` and publish one normal listing for each
   household account.

## Remaining open decisions

- Whether reliable new app-created listings justify importing or managing existing
  fixed-price listings.
- Whether actual usage justifies changing the GPT-5.5 baseline or the $8 OpenAI limit.

## Unverified external assumptions

- The exact database error that caused the 2026-09-01 Production save and eBay-connection
  failure is unknown because the earlier deployment discarded the database exception and
  remote console access was denied. New safe diagnostics and the listing-store health
  check will classify a recurrence.
- The corrected application-token path has not yet passed an authenticated Production
  category check after one web process has run for more than two hours.
- Production credentials, OAuth scopes, business policies, postal code, and the
  notification subscription are correctly configured in the deployed service.
- A Trading-created Production listing contains every expected field and ordered photo
  and remains editable in Seller Hub. Automated tests cannot prove this.
- M7 has no recorded maximum-request capacity result or accepted numeric latency limit.
  OA-011 remains open until the named benchmark supplies that evidence.
- Categories used in normal household work do not require excluded variation,
  regulatory-document, or special-condition-descriptor flows.

## Canonical references

- [Product plan](docs/ebay-listing-assistant-plan.md)
- [Direct Trading publication decision](docs/decisions/0006-seller-hub-drafts-and-trading-adapter.md)
- [Repository guidance](AGENTS.md)
- [Accepted decisions](docs/decisions/README.md)
