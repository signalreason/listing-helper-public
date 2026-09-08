# 0007 — Save unfinished listing drafts

- Status: Accepted
- Date: 2026-08-15

## Context

A user can finish the paid photo analysis and then encounter an eBay category,
authorization, photo-upload, or handoff error. Before this decision, the app saved an
eBay-ready snapshot only after the Seller Hub handoff started. An earlier error could
force the user to run the OpenAI generation again.

## Decision

Save each successful generated listing draft in PostgreSQL before returning it to the
browser. Autosave later listing edits and selected eBay category and condition data.
Show unfinished drafts to their owner so they can reopen or delete them.

Do not save photo bytes or filenames. A reopened draft requires the user to select the
original photos again unless a prior attempt already created a complete set of valid
eBay Media API references. Keep the unfinished source draft after every failed or
uncertain request. Remove it only when the user deletes it or eBay confirms direct
publication with an item ID. Keep the eBay-ready snapshot, media references, mapping,
and historical handoff state needed for recovery.

## Consequences

- A user can recover listing data without another OpenAI request.
- Saved drafts are private to one app account and do not form a completed-listing
  history.
- Repeated publication attempts can reuse valid eBay media references and the stable
  app mapping.
- The existing PostgreSQL service is sufficient. No object store, queue, or new paid
  service is added.

## Amendment — 2026-09-01

Decision 0008 supersedes the photo-retention and completion rules above. The
originating browser keeps device-local photos. A confirmed publication marks its
source draft published and read-only but does not remove it. Only user deletion removes
the source draft and its browser photos.
