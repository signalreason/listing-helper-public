# 0001 — Browser-first, single-service product

- Status: Accepted
- Date: 2026-08-05

## Context

The product succeeds when a user can reach a useful eBay listing draft with almost no
setup. The retired architecture optimized for an infrastructure constraint instead of
this user outcome.

## Decision

Build the product as a responsive browser experience backed by one deployable application.
The application accepts photos directly, calls OpenAI, and returns a listing draft in
the same interaction.

Kafka, queues, separate workers, watched folders, and desktop packaging are not part of
the baseline. Add a component only after observed product or operational need justifies
it. Consider a desktop wrapper later only if daily browser use exposes friction that a
wrapper would remove.

## Consequences

- The first implementation is one thin vertical slice rather than a distributed
  pipeline.
- Users need only a browser; no local folder convention or companion process exists.
- Long-running background work, durable history, and horizontal job processing are
  deferred.
- The internal implementation may be replaced cheaply while the user workflow is
still being refined through daily household use.
