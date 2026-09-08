# Repository constraints

These are durable product and architecture constraints that do not need to be loaded
for every repository task.

Read the sections relevant to the area being changed.

## Product

The application is a small production tool for at most two trusted household users,
primarily apparel resellers.

The primary workflow is:

1. Open the browser application.
2. Upload listing photos.
3. Select a category group and an eBay category from its menu.
4. Generate listing information using current category rules.
5. Review and edit the draft.

Ease of use has priority over configurability. Do not introduce pilot programs,
focus groups, enterprise controls, speculative SaaS features, or other machinery
intended for a larger customer base without an accepted product change.

Users must not need ChatGPT, Codex, an OpenAI account, an API key, companion
software, watched folders, marker files, or command-line steps.

Generated listing drafts must clearly distinguish uncertain information from facts.
Pricing guidance is an estimate and includes confidence and rationale.

## Application architecture

Keep the product as one deployable application unless an accepted decision changes
this architecture.

Baseline stack:

* Python 3.13
* `uv`, `pyproject.toml`, `uv.lock`, `.python-version`
* FastAPI
* Uvicorn
* semantic HTML/CSS/vanilla JavaScript
* Pydantic
* official OpenAI Python SDK
* Pillow and `pillow-heif`
* pytest
* FastAPI test client
* Playwright Python
* Ruff

Do not introduce React, Next.js, Vite, Node.js build tooling, a template framework,
Docker, background workers, or a queue without demonstrated need and an accepted
architectural change.

Deployment baseline is one DigitalOcean App Platform service plus its PostgreSQL
database.

Infrastructure plus OpenAI API usage should remain at or below $20/month.

## OpenAI

All OpenAI requests originate on the server.

Read `OPENAI_API_KEY` from the runtime environment only when an OpenAI request
requires it.

Never expose or copy the key into:

* application configuration,
* browser code or responses,
* desktop clients,
* logs,
* errors,
* generated artifacts,
* telemetry,
* tests,
* or the repository.

GPT-5.5 with medium-quality JPEG inputs is the known-good generation baseline.

Do not change the model or image fidelity without representative comparisons showing
equal or better business results at acceptable cost.

Live OpenAI tests must remain opt-in.

## Uploads and images

Accept 1–24 photos with a maximum size of 12 MB per photo.

Normalize eBay-supported image formats server-side when required by OpenAI.

Stream uploads to temporary disk and process images sequentially. Do not buffer the
theoretical maximum request in application memory.

Server-side source and normalized photos are private and request-scoped. Decision 0008
permits original photos in device-local browser storage until the user deletes their
draft. Do not add server photo persistence without another accepted decision.

Never commit real user photos or generated private listing data.

## Accounts and persistence

Require individual email/password accounts.

The owner creates accounts. The server generates the initial random password and
displays it once with a copy action.

Allow at most two active users.

Per account:

* 24 generations per hour;
* 30 generations per day;
* one active generation at a time.

Allow no more than two active generations globally.

Persist account data and saved draft records required for login and recovery.

Published drafts and device-local photos follow decision 0008. Do not add server photo
persistence or other completed-listing history without an accepted product change.

## Testing boundaries

Changed behavior should have deterministic coverage appropriate to its risk.

Important failure boundaries include:

* invalid uploads;
* oversized uploads;
* OpenAI failures;
* malformed model output;
* secret exposure;
* critical mobile/browser behavior.

Performance requirements must identify the benchmark, dataset, hardware, metric, and
limit. If evidence does not justify a numeric limit, record it as unverified rather
than inventing one.
