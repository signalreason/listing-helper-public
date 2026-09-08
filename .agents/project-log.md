# Project learning log

This is a small working memory for project decisions, issues, root causes, and useful
automation candidates. It is not a full history. Read it after `AGENTS.md` and
`PROJECT_STATE.md` and before repository work.

## How to use this log

- Add one concise entry for each non-trivial task decision and each unexpected
  failure, wrong result, or discovered defect.
- For an issue, include the observed result, the verified root cause, the corrective
  action, and the durable prevention. Use `unverified` when evidence is not complete.
- For a decision, include the reason and the canonical artifact that owns the result.
- Put executable behavior in code and tests, accepted cross-task decisions in
  `docs/decisions/`, stable knowledge in `docs/knowledge/`, current status in
  `PROJECT_STATE.md`, repeated commands in `bin/`, and repository rules in
  `AGENTS.md`. Keep only a short pointer here.
- Never include credentials, environment values, private photos, private listing
  data, or large command output.
- Before each commit and handoff, review open entries, merge duplicates, and remove
  resolved detail after a durable artifact owns the prevention. Keep no more than 10
  resolved entries and 200 total lines. Git history keeps older detail.

Use this form:

```text
### YYYY-MM-DD - Short name
- Kind: decision | issue | automation candidate
- Status: open | resolved | unverified
- Observed or decided:
- Root cause or reason:
- Action:
- Prevention or canonical owner:
```

## Open issues

### 2026-09-01 - Draft save and eBay connection failed together

- Kind: issue
- Status: unverified
- Observed or decided: A generated Production draft showed `Not saved`, and the page
  stayed at `Checking your eBay connection` with no visible publish action.
- Root cause or reason: The automatic draft save returned the existing typed
  `EbayStoreUnavailable` failure, and the publish UI was hidden until the connection read
  succeeded. The exact PostgreSQL failure is unverified because the old code discarded
  its cause, emitted no diagnostic event, and Production console access returned HTTP 403.
- Action: Keep the draft and disabled publish action visible with recovery instructions,
  classify and safely log draft-store failures, and include the listing tables in the
  platform health check.
- Prevention or canonical owner: Server and phone-browser regression tests own save
  recovery, visible publication gates, safe diagnostics, and listing-store health. The
  first focused browser run failed because one text locator matched both the connection
  card and publish message; scoping the assertion to the publish status fixed the test.
  Review found that the old disconnect handler still hid the publish panel; rendering
  the disconnected gate there and adding phone coverage closed that second path.

### 2026-08-17 - Seller Hub rejects one EPS photo URL

- Kind: issue
- Status: unverified
- Observed or decided: A Seller Hub Reports draft CSV failed immediately when it
  included one EPS photo URL that contained `~`. The same row succeeded without that
  URL. Encoding the character as `%7E` did not help.
- Root cause or reason: The operator accepted `~` as the provisional cause and chose
  not to run another external test. Seller Hub did not return a row error, so the
  vendor-side cause remains unverified.
- Action: Historical Seller Hub CSV generation omits a complete photo URL that
  contains `~`. Direct Trading publication keeps Media API URLs unchanged.
- Prevention or canonical owner: Draft-feed and Trading XML tests own the boundary.
  Seller Hub draft photos remain optional and omitted photos can be added there.

## Automation candidates

None.

## Recent resolved entries

### 2026-09-08 - Prepare source for public use

- Kind: decision
- Status: resolved
- Observed or decided: Separate reseller guides from maintainer records. Supply public
  privacy contact details through runtime settings and keep deployment values out of Git.
- Reason: A reusable installation must not present another operator's identity. The owner
  confirmed MIT and a new repository with clean history; retain the original privately.
- Action: Add setup, operating, and release guides, source checks, and safe secret scanning.
- Prevention or canonical owner: `docs/public-release.md` owns the release procedure;
  privacy route tests and `tests/test_public_checks.py` cover missing contact, escaping,
  private artifacts, scanner errors, and suppression of raw findings.
- Tooling failures: Shell query expansion and a nested heredoc prevented file writes.
  Use quoted queries and structured patches. The log also exceeded its 200-line cap;
  remove superseded resolved detail before validation. The existing validator owns the cap.

### 2026-09-05 - Category menu empty after deployment

- Kind: issue
- Status: resolved
- Observed or decided: The user reported an empty group menu after the category release.
- Root cause or reason: The new HTML reused the prior script address. Production
  assets had no explicit cache lifetime and a fixed 1980 Last-Modified date. A browser
  cache regression reproduced the empty menu when the old script accessed a removed
  control. The user's exact cache state remains unverified because UI access was blocked.
- Action: Change the listing script, stylesheet, and imported photo-store addresses;
  serve HTML and static assets with `Cache-Control: no-store`.
- Prevention or canonical owner: `tests/test_main.py` covers normal and conditional
  cache responses. The browser regression primes the real HTTP cache with the prior
  control access, then verifies all three groups and a category list after the update.

### 2026-09-05 - Stored category groups

- Kind: decision
- Status: resolved
- Observed or decided: Reuse successful public category lists for Men, Women, and Kids.
- Root cause or reason: T1 requires storage across requests and restarts.
- Action: Use the existing PostgreSQL service, with environment/tree/group keys.
- Prevention or canonical owner: Migration 0008 and `tests/test_category_groups.py`
  cover storage, group isolation, leaf identity, concurrent reads, and failure retry.
  ASM-1 remains unverified; current aspect checks continue to use eBay rules.
  T2/T3 browser tests cover menu layout, retry, saved group identity, and stale loads.
  T4 tests cover all group/type pairs and remove unknown-source generated values
  before dependent values are rechecked. Seller edits remain outside this cleanup.
  T5 keeps a sticky `generation_required` field in draft JSON. The PostgreSQL upsert
  sets it atomically on category changes; a seller PUT cannot clear it. Decision 0006
  owns the behavior. Memory, disposable PostgreSQL, route, and browser tests cover it.
  Keep the app-only group field out of raw Taxonomy test fixtures; the shared fixture
  initially violated that strict model. Separate fixture construction now covers it.
- Unexpected failure: One full browser run again missed a photo before generation.
  A deterministic delayed-module test verified that a selection before the change
  handler loads was lost. Consume any existing file selection at module startup.
  The browser regression owns this boundary; the exact timing of old runs is unverified.

### 2026-09-05 - Decompose the category-menu story

- Kind: decision
- Status: resolved
- Observed or decided: Store five behavior tasks with stable clause IDs and explicit dependencies.
- Root cause or reason: Task decomposition must stay separate from repository context compilation.
- Action: Add the supplied prompts, the candidate tool, and the story task files.
- Prevention or canonical owner: [The story bundle](../stories/2026-09-05-category-before-generation/README.md) owns the clauses, task coverage, and filename rule. No feature code changed.
- Review findings: Supplied discovery omitted source types, instruction scopes, safe
  date handling, complete clause text, and common imports. It also used unrelated task
  text, inconsistent output limits, and alphabetical dependency order. Links could
  expose content outside the repository.
- Root cause and prevention: Incomplete patterns, whole-task scans, link-following
  reads, and early caps caused these gaps. Structured inputs, scoped rules, bounded
  reads, ranked evidence, and CLI regression tests now own these boundaries.
- CLI tests also cover invalid UTF-8 input and Unicode excerpt offsets.
- Import scans now skip Go comment and literal examples; CLI tests cover real imports.
- Review scope expanded beyond the story request. `AGENTS.md` now owns priority-based review triage and the acceptance/CI gates.

### 2026-09-04 - Select category before generation

- Kind: decision
- Status: resolved
- Observed or decided: Sellers select the eBay category before the single model request.
  Generated names follow current rules; invalid generated values are omitted and missing
  required facts remain visible in an incomplete draft.
- Root cause or reason: The old model request had no category rules. Later validation
  retained unsupported names and required the seller to remove them.
- Action: Bind generation and initial persistence to the selected category. Keep the
  generation selection separate from the open draft's publication category.
- Prevention or canonical owner: Decision 0006, FR-004/FR-005a, server tests, and browser
  tests own the flow, stale-search guards, retries, and seller-data preservation.
- Unexpected results: The new region name matched an old broad browser locator; exact
  field labels resolve it. Published-state locks initially kept category controls
  disabled after Start new listing; shared readiness recalculation resets them.
  Readiness recalculates after draft opening and deletion finish. Restoring a saved
  category must not queue a PUT before its rules load; an unavailable lookup must leave
  clean drafts navigable when browser storage is unavailable. The existing recovery
  test owns this boundary. Old tests now request alternative categories explicitly.
  Playwright can omit multipart upload bodies; the real generation response verifies
  the selected category without depending on that inspection behavior.

- Integration: The starting checkout was behind remote main; fetch the target before
  creating a task branch. Main added required-product support, optional prior-draft autosave,
  and browser reconciliation. Retain the new save behavior and required-field controls;
  restrict browser cleanup to untouched new output so later category changes preserve
  seller data. Current accepted rules also permit generating PRODUCT fields; a PRODUCT
  marker alone is not an unsupported field. The merge adds no extra model calls.
  Recovery locks protect the old draft, not new generation. A regression test found old
  policy locks restored onto the new draft; rendering now discards the old control snapshot.

### 2026-09-03 - Item-specific form required manual cleanup

- Kind: issue
- Status: resolved
- Observed or decided: A generated `Pockets` row told the seller to remove it, while a
  blank required `Inseam` row showed two instructions and a Remove action.
- Root cause or reason: The browser preserved model fields after category rules rejected
  their names, and both row and missing-aspect checks reported the same required blank.
- Action: Reconcile rows to valid category names, protect and label blank required rows,
  record their entered values as seller-provided, and report one field error.
- Prevention or canonical owner: Decision 0006 and focused server and browser tests own
  reconciliation, placeholders, attribution, and strict server checks. Review found that
  free-edit fallback cleared pending attribution and duplicate required rows had no removal
  path. Provenance now survives free editing, and duplicates stay removable until one
  remains. A later review found duplicate accessible labels after row removal; all affected
  controls now reindex. Tests use supported DOM value reads and exact required-row waits.
