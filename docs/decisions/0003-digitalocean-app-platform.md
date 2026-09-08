# 0003 — DigitalOcean App Platform deployment

- Status: Accepted; storage scope amended by decisions 0006 and 0007
- Date: 2026-08-05

## Context

The two-user production app needs a normal HTTPS browser experience with minimal
operator work and a combined infrastructure and OpenAI API budget of $20 per month.
At the time of the original decision, photos and listing drafts did not need persistent
storage, but individual accounts did. The amendment below records the later storage
scope.

## Decision

Deploy one web service to DigitalOcean App Platform from the Git repository. Start with
the `apps-s-1vcpu-0.5gb` plan: one shared CPU, 512 MiB RAM, 50 GiB monthly bandwidth,
and a current price of $5/month. Use the platform URL and managed TLS.

Add one App Platform development PostgreSQL database at its current $7/month price.
The original scope was accounts and authentication state. App Platform has no
persistent local filesystem or volume support, so SQLite in the web container is not
a reliable account store.

Configure `OPENAI_API_KEY` as an encrypted, run-time-only environment variable. Stream
uploads to the platform's temporary filesystem, normalize images sequentially, and
delete request files after completion. Do not hold the theoretical 288 MB maximum
upload in memory.

Create a dedicated OpenAI project with an $8 monthly hard spend limit plus lower spend
alerts. This makes the intended maximum $5 hosting + $7 database + $8 API usage =
$20/month. Generation rate limits protect server capacity, while the OpenAI project
limit is the cost backstop.

## Consequences

- Git-based deployment, managed HTTPS, and encrypted runtime variables reduce setup
  and maintenance work.
- The default platform domain avoids a domain purchase for now.
- The 512 MiB tier requires streamed multipart handling, sequential image conversion,
  bounded concurrency, and temporary-file cleanup.
- A hard OpenAI spend limit can interrupt generation with a budget error; the UI
  must turn that into a clear budget-unavailable message.
- The development database is intentionally a low-cost operational tradeoff. It has no
  built-in backups and is not DigitalOcean's production-grade managed database. A
  database loss can remove accounts and the records added by the amendment below.
- Hosting prices can change, so recheck the plan before deployment or tier changes.
- Object storage, workers, and other platform components are excluded.

## Amendment

Accepted [decision 0006](0006-seller-hub-drafts-and-trading-adapter.md) and
[decision 0007](0007-save-unfinished-listing-drafts.md) extend the same PostgreSQL
service to unfinished drafts, encrypted eBay authorization, listing mappings,
API-neutral snapshots, media references, and recovery state. The application still
does not store photo bytes, and this amendment does not add another service. The owner
can recreate the two accounts, but a database loss can require eBay reconnection and
manual recreation of unfinished work. This is an accepted low-cost tradeoff for the
two household users and must be reviewed before broader use.

## Evidence

- [DigitalOcean App Platform pricing](https://docs.digitalocean.com/products/app-platform/details/pricing/)
- [DigitalOcean App Platform limits](https://docs.digitalocean.com/products/app-platform/details/limits/)
- [DigitalOcean App Platform data storage](https://docs.digitalocean.com/products/app-platform/how-to/store-data/)
- [DigitalOcean App Platform databases](https://docs.digitalocean.com/products/app-platform/how-to/manage-databases/)
- [DigitalOcean encrypted environment variables](https://docs.digitalocean.com/products/app-platform/how-to/use-environment-variables/)
- [OpenAI spend limits](https://developers.openai.com/api/docs/guides/spend-limits#choose-a-spend-control)
