# 0008 — Keep draft photos in the originating browser

- Status: Accepted
- Date: 2026-09-01

## Context

Saved drafts survive a page reload, but their photos previously survived only while
the page stayed open. A later publication attempt required another file selection.
Server photo storage would add cost and private-data operations that this two-person
tool does not need.

## Decision

Keep each saved draft's original photos in IndexedDB for the browser profile and site
that created it. Store the draft UUID, originating account ID, photo order, filename,
media type, size, modification time, bytes, and monotonic server draft revision. Reject
an older local write and load photos only when their revision matches the opened server
draft. Require each draft PUT to send that loaded server revision, and reject the write
if another tab already advanced it. A null revision is valid only when the initial
generation save failed and no server draft exists. A text-only autosave advances only this revision metadata. Replace stored photo
bytes only after a photo edit. If that photo write fails, treat the draft save as
incomplete and block saved-draft navigation that would discard the in-memory photo edit.
Also block draft saves and saved-draft navigation while the seller has selected only part
of a missing original photo set. The same block does not apply to a clean reopened draft
that has no local photo copy. A new generation can continue after either incomplete-save
case. It creates a separate draft from the currently selected photos, while the prior
draft stays at its last successfully saved revision. The account ID prevents accidental
cross-account access.
If a photo changes while an earlier save is still in flight, keep that earlier save's
incomplete result for its caller and run a new save for the newer local revision. Serialize
the full save operation before the tombstone read so concurrent callers cannot send the
same loaded server revision. Before saved-draft navigation calls this save operation,
let a draft with no edits and no local photo copy navigate when IndexedDB is unavailable.
This navigation exception cannot send a PUT or write local photos. Other operations,
including publication, must still check the tombstone.
One browser profile per app account is the supported use case; use by a second account
in the same profile is unsupported.

Apply one 1 GiB limit to all draft photo bytes in that browser profile. Show current
draft usage and total browser usage. Warn above 75% and again at 900 MiB. Reject a new
generation before its OpenAI request when its photos would take the total above 1 GiB.
Reserve that capacity in a serialized IndexedDB transaction before the request. Count
active reservations during other writes, and expire abandoned reservations after 15 minutes.
Browser quota and storage-pressure failures can impose a lower practical limit.

Browser photo storage is a convenience copy, not a backup. It does not synchronize
between devices. Clearing site data, private browsing, or browser eviction can remove
it. Record an account-scoped deletion intent in the IndexedDB tombstone before deleting
a server draft. Before waiting for that intent, detach and freeze the open draft so a
late edit cannot start an autosave. Restore it only if the tombstone is not established
or the server gives a definite rejection and this attempt's tombstone is cleared. Do not
try to clear a tombstone when this attempt failed to write one. Reject all photo
input while deletion is in progress. If deletion cancels a pending autosave and the
server keeps the draft, restore and requeue that autosave. The same
transaction protects the tombstone and intent from whole-array
updates in other tabs. The tombstone rejects delayed writes. Retry the server and local
photo deletions after an uncertain interruption. Give each deletion attempt an owner
token, and let only that owner reverse its tombstone after a definite server rejection.
Keep the tombstone for network, retryable, or server failures that can have an uncertain result.
On startup retry, clear the owner-matched tombstone after a definite non-retryable
server rejection so the draft becomes available again.
Remove an uncertainly deleted draft from the editable form and saved-draft list until startup
reconciles the cleanup. Filter pending tombstones on every saved-draft refresh, including
when a startup retry remains uncertain. Reload that set from IndexedDB on each refresh
so a tombstone written by another tab hides the server draft. Recheck IndexedDB before
each draft PUT. Block the save if this check fails, and close the editor without saving
if another tab has a pending tombstone. Recheck before a saved-draft GET and again before
rendering its response, so a stale Open control cannot expose a draft under deletion.
Permit only one open operation at a time. Before its first wait, freeze the current
editor and invalidate pending category, requirement, and business-policy loaders. Defer
new draft-specific loaders from a late eBay connection response. Keep the open lock
through the target render and final storage display read, including controls created by
the render. Recheck this lock and all other listing-action locks after each asynchronous
photo capacity check, before adding a photo. Version draft-list and storage-display refreshes so an older response cannot
replace newer browser state. If IndexedDB is unavailable, still permit server draft
deletion and server-only generation. Apply the same fallback if an open database
becomes unusable or cannot write the deletion tombstone. Stop local tombstone refreshes
after that failure so the fetched server draft list remains available. If the tombstone
refresh is the first failing transaction, mark storage unavailable and still render the
already-fetched server records. Tell the user that a possible local photo copy needs
site-data cleanup. Do not PUT an existing server draft
when the database did not open, because an unreadable tombstone can still exist. A new
server-only generation can create its new UUID before it has a loaded server revision.
Server uploads and normalized images remain request-scoped and temporary.

Keep a source draft after confirmed publication. Mark it published, make it read-only,
and retain its local photos until the user deletes the draft. Deleting a published
draft removes its local photos and server draft but preserves the live eBay mapping;
it does not change or end the eBay listing.

Require the browser to send its loaded revision and to store its current photo selection
at that exact server revision before publication preparation. Bind preparation to that
source revision. Write the prepared listing
snapshot and source revision atomically under the same locks. Bind each media write to
that revision, including a changed revision from a retained historical transfer. Starting
a new generation invalidates earlier category loaders so they cannot save into the new
result. Before an eBay publication request, lock and recheck that revision, mark
the listing as publishing, and reject
draft saves or deletion until publication finishes. Recheck the revision when the live
item mapping is committed. Acquire the lease before loading the listing snapshot and
photo URLs. Return the source revision and a unique preparation ID, require the caller to
send both for publication, and fence media writes and lease acquisition to them. This
also rejects a legacy unversioned preparation and its old bodyless publish request. This
prevents another tab from replacing reviewed policies at the same source revision. Then
reject preparation updates after the listing has an eBay item ID. Reload under that lease
and reject expired references before any eBay call. Use a 15-minute publication lease. Fence
the add attempt, cleanup, and live completion to the exact lease acquisition. Clear it after a
definite verification or publication rejection, but retain it when eBay's
publication result, protected reload, or live-state commit is uncertain. After an interruption, an
expired lease resumes the unchanged preparation with the same idempotent eBay request
ID instead of replacing or deleting its recovery data. Detect this matching protected
preparation before validation and use its stored snapshot instead of mutable current
package, category, or business-policy data. If saved media URLs expire, a
stale lease can refresh them from the original revision-bound browser photos. When the
browser requests stale recovery, require the protected preparation ID and reject a clean
tab that does not have it. When the
protected server draft rejects the browser's normal pre-publication save, allow recovery
only if the open draft has no unsaved changes. Keep the business-policy selectors frozen
at the exact prepared policy IDs while an uncertain publication recovery is pending,
including after a pre-publication draft save fails. If a prepared policy is no longer in
the seller's current policy list, show its protected ID as a frozen saved option and do
not let mutable-policy validation block recovery.
Expose that protected state and its prepared policy IDs when a seller reopens the draft.
Keep those IDs in current draft state so a delayed eBay connection load restores them.
Restore the saved category and condition and the protected policy IDs without calling
mutable eBay category, requirement, or policy loaders. Loader availability and current
metadata must not block a retry that uses the protected server snapshot.
Keep the recovery identity after a transient preparation failure. Clear it only when a
preparation or publication response proves that protected recovery is no longer valid.
On a clean reopen, do not treat automatic category restoration
as a seller edit or try the blocked autosave before recovery. An expired-photo response
identifies when an earlier add attempt still owns protected recovery, so the browser keeps
policies frozen. Distinguish fresh verification and add rejections from protected retry
rejections. A verification transport failure also uses a protected recovery code when an
earlier add result is uncertain. Treat a lost publish response or an unknown publish error
as protected because the add result can be uncertain. This keeps the retained draft consistent with the data sent to eBay and
prevents an untracked live listing.

## Consequences

- A draft opened in its originating browser normally has its ordered photos ready.
- The same server draft opened in another browser has no local photos.
- Published drafts form a user-controlled history until deletion.
- No object store, server photo persistence, new service, or paid dependency is added.
- Decisions 0006 and 0007 are amended where they require deletion after publication
  or prohibit device-local photo persistence.
