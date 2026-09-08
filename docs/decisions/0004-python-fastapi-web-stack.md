# 0004 — Python and FastAPI web stack

- Status: Accepted
- Date: 2026-08-05

## Context

The product needs one maintainable web service that fits DigitalOcean App Platform's
512 MiB tier. It must serve a small responsive page, accept as many as 24 image
uploads without buffering the whole request, normalize every eBay-supported image
format, call OpenAI on the server, and validate a structured listing result.

The stack should minimize user and operator setup. In particular, common phone HEIC
photos must work without asking users to convert them and without requiring a custom
container solely for image support.

## Decision

Use this stack for the product:

- **Runtime and packages:** Python 3.13, with `uv`, `pyproject.toml`, `uv.lock`, and
  `.python-version` providing a reproducible environment.
- **Web application:** FastAPI on Uvicorn, deployed as one process with one worker
  initially. Use FastAPI `UploadFile` inputs and bounded chunked reads so upload bytes
  can spill to temporary disk rather than accumulating in application memory.
- **Browser UI:** one semantic HTML page with CSS and vanilla JavaScript modules,
  served as static files by the same FastAPI application. Do not add React, Next.js,
  Vite, a Node.js build, or a server-side template framework to the baseline.
- **Images:** Pillow plus `pillow-heif`. Inspect actual file contents, enforce the
  count and byte limits while reading, process images sequentially, remove metadata,
  and normalize unsupported OpenAI inputs to JPEG, PNG, or WebP in request-scoped
  temporary storage.
- **Application contracts:** Pydantic models define the listing result and OpenAI
  Structured Outputs schema. Use the official OpenAI Python SDK and the Responses API.
  Construct the OpenAI client in the generation request path using only
  `os.environ["OPENAI_API_KEY"]`; keep the model name in a non-secret environment
  setting.
- **Tests and checks:** pytest with FastAPI's test client for application behavior,
  a small set of Playwright Python tests for the critical phone and desktop browser
  flow, and Ruff for formatting and linting. Paid OpenAI calls remain opt-in.
- **Deployment:** use DigitalOcean's Python buildpack rather than a Dockerfile. Start
  Uvicorn on `0.0.0.0` and the platform-provided `PORT` with one worker; add workers or
  a container only after measurements require them.

Keep the first code layout small: `app/` for the server, image handling, models, and
static page; `tests/` for deterministic tests; and the three Python environment files
at the repository root. Split modules further only when code size or ownership makes
the separation useful.

## Consequences

- The browser requires no installation and the deployment contains one runtime, one
  process, and no client build pipeline.
- FastAPI and the OpenAI Python SDK can share Pydantic models across HTTP validation
  and model-output validation instead of maintaining parallel schemas.
- `UploadFile` spooling and sequential Pillow processing support the 512 MiB target,
  but maximum-request memory and temporary-disk behavior still require measurement on
  App Platform.
- `pillow-heif` provides a direct Python path for HEIC/HEIF and AVIF photos. Its binary
  wheel must be verified in the deployed vertical slice before compatibility is
  considered proven.
- A single worker intentionally bounds resource use and concurrency. Authenticated access and
  request-rate limits must prevent one instance from accepting more simultaneous
  image work than it can safely process.

## Alternatives considered

- **Node.js with Fastify** would also provide a small server, but Sharp's prebuilt
  binaries do not include patent-encumbered HEIC support. Reliable HEIC handling would
  add another converter or a custom libvips/container build, increasing deployment
  work for a core input format.
- **Go** would produce a compact service but requires more manual multipart, image,
  and Structured Outputs integration for this product.
- **A React or Next.js client** adds a second toolchain and build lifecycle without
  simplifying this one-page interaction.

## Evidence

- [DigitalOcean Python buildpack](https://docs.digitalocean.com/products/app-platform/reference/buildpacks/python/)
- [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/)
- [FastAPI static files](https://fastapi.tiangolo.com/tutorial/static-files/)
- [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [pillow-heif installation](https://pillow-heif.readthedocs.io/en/stable/installation.html)
- [pillow-heif Pillow plugin](https://pillow-heif.readthedocs.io/en/stable/pillow-plugin.html)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [`uv` project synchronization and locking](https://docs.astral.sh/uv/concepts/projects/sync/)
- [Sharp installation and HEIC limitation](https://sharp.pixelplumbing.com/install/)
