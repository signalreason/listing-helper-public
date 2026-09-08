# Developer guide

The app is one Python 3.13 FastAPI service with PostgreSQL and a browser interface.
Sellers should start with the [reseller guide](user-guide.md).

## Local setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and PostgreSQL 17.
Create a local database owned by your local database role:

```sh
createdb ebay_listing_assistant
uv sync --locked
```

The app does not load `.env` files. [.env.example](../.env.example) is an empty reference.
Set the process environment through your password manager or private terminal session.
Use a password prompt instead of putting secret values in shell command arguments.
Set `DATABASE_URL`, `SESSION_SECRET`, `APP_SETUP_TOKEN`, `APP_OPERATOR_NAME`, and
`APP_PRIVACY_EMAIL`. See the [settings table](setup.md#2-set-the-runtime-values).
Your local PostgreSQL connection URI must name the `ebay_listing_assistant` database.
Authentication depends on your PostgreSQL installation.

```sh
uv run python -m app.migrate
SESSION_COOKIE_SECURE=false uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Open [the local login page](http://127.0.0.1:8000/login), create the owner, and retain the
password. This starts accounts and the UI. Real generation also needs the OpenAI and
eBay settings. Normal tests use fake providers and need no credentials. Public eBay
callbacks require HTTPS; local HTTP is not a replacement for hosted eBay setup.

## Validation

```sh
bin/validate-repo
uv run playwright install chromium
uv run pytest -m browser
bin/check-secrets
```

The first command checks public source files, links, project records, the lockfile,
formatting, lint, and deterministic tests. Browser tests use fake providers. They do not
spend API credits or create listings. The secret scan requires
[TruffleHog 3.97.4](https://github.com/trufflesecurity/trufflehog/releases/tag/v3.97.4)
and complete Git history. It scans unignored source files and the history reachable
from the current checkout. It does not inspect other agents' branches, verify found
credentials against providers, or print their values.

`bin/check-public-files` rejects common private output files, local user paths, and
non-example email addresses. It is a limited guard, not a complete personal-data
detector. Use `example.com`, `example.org`, or `example.net` in public examples.
Review screenshots manually. Keep raw scans, traces, and reports out of Git.

GitHub Actions runs validation, browser tests, and secret scanning on pull requests.
It receives no production credentials and uploads no test artifacts.

## Changes and design records

Read [AGENTS.md](../AGENTS.md) before changing code. Use an isolated worktree, deterministic
tests, and a pull request. See [PROJECT_STATE.md](../PROJECT_STATE.md) for implementation
status and open checks. [Accepted decisions](decisions/README.md) and the
[product plan](ebay-listing-assistant-plan.md) own product and architecture rules.
Agent prompts and story files are maintainer tools, not seller setup requirements.

Before publishing source, follow [the public-release procedure](public-release.md).
