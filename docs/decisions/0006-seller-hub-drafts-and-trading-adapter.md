# 0006 — Direct Trading publication with historical CSV recovery

- Status: Accepted
- Date: 2026-08-10

## Context

The photo-to-draft flow is reliable in daily use. The next useful step is to remove
manual copying without making the app the only recovery path for a live listing.

Listings created with eBay's Inventory API cannot be edited in Seller Hub. The app is
not yet ready to own every live-listing operation. eBay still supports the core
Trading API calls for fixed-price listing creation, verification, retrieval, revision,
ending, and relisting. However, eBay is moving older support functions away from the
Trading API. The deprecated `UploadSiteHostedPictures` call is one example.

eBay documents `FX_LISTING` rows with `Action=Draft` for editable Seller Hub drafts.
In Production, the Feed API returned `BAF.Error.5` for an app row and retained the
exact submitted bytes. The same timestamped file created a draft when the user
uploaded it through Seller Hub Reports. Changes to the optional custom label and file
name did not change the API result. The file contract is valid, but this account's
Feed API path is not a reliable transport for Draft actions.

## Decision

Use the Trading API to publish the reviewed saved draft as a live fixed-price listing.
Do not create new CSV or Feed handoffs. Keep authenticated status and download routes
only for historical transfer records.

The server builds the complete eBay record from the saved draft, selected category and
condition, three existing business policies, and the shared `EBAY_ITEM_POSTAL_CODE`
runtime value. The seller enters a positive package weight in whole pounds and ounces,
and positive package length, width, and height in whole inches. The app saves these
values with the unfinished draft. Trading API maps height to
`ShippingPackageDetails.PackageDepth` and sends all three dimensions with the weight.
It sends every reviewed item specific as structured `ItemSpecifics.NameValueList`
data. It sends 1–24 Media API URLs in the selected order.
This follows eBay's [package dimensions and weight](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/shipping-package-size.html)
contract for US listings.
Before generation, the seller selects a group (Men, Women, or Kids) and then a
category from its menu. Each menu includes the group's clothing, shoes, bags, and
accessories. Successful public category lists use the existing PostgreSQL store,
keyed by environment, US tree, and group. A missing list loads its eBay subtree;
failed loads remain retryable. No scheduled refresh is added. The story's fixed-taxonomy
assumption remains unverified; current aspect checks are unchanged.
The server loads current category rules before spending generation quota or making
its one model request. Model output names are constrained to supported seller-set
aspects; arrays hold generated values. Invalid specifics and generated values marked
unknown are omitted and
conditional dependencies are rechecked after each removal. Missing required facts
remain visible in a usable incomplete draft. This cleanup applies only to new model
output, never to saved drafts or seller edits. The selected category is saved with the
initial draft and returned even if that save fails. The saved category includes its
group ID, so reopening restores both menus through the shared category-list API.
Later menu selections update the editable draft's category; protected publication
recovery retains its original snapshot. Older drafts retain their category while
the app checks actual group membership. No title-based category selection
runs after generation. A later category change marks the old generated draft as
requiring generation. Keep its fields for review and copy, but block publication.
The server retains this state on each save, including a return to the original
category. A draft PUT cannot clear it. Only a successful explicit Generate listing
action creates a new valid draft for the selected category. Failed generation keeps
the old draft and its state. This follows decision 0008's separate-draft generation
rule; it does not rewrite or repair the old item specifics.
Category search uses application authorization and does not require seller OAuth or
enabled publication. New generation has no fallback when category rules are unavailable.
Existing drafts retain their copy and edit recovery paths.

The live Taxonomy response defines the seller-set item-specific names and their
required status, usage, mode, cardinality, maximum length, data type, format,
applicability, and conditional values. The browser loads these rules when the category
changes, and the server loads them again before publication. A draft can remain
incomplete, but every current Taxonomy rule must pass before photo transfer begins.
The app does not attach an eBay catalog product. It therefore accepts `ITEM`, `PRODUCT`,
and combined applicability and sends each reviewed aspect through
`ItemSpecifics.NameValueList`. `PRODUCT` prevents a seller override only when a listing
is attached to a catalog product. An unknown applicability value remains unsupported.
For a conditionally available value, every returned control-aspect dependency must
match. Any listed value within one dependency can satisfy that dependency.
Aspect names are read-only while current category rules are active. If eBay rules are
unavailable or publication is not configured, the copy-only draft flow keeps free name
editing and custom aspect entry. These fallback fields do not bypass publication checks.
On the initial category load for a new generation, the browser removes untouched
model specifics that are not listed for that category. It preserves seller edits and
all fields on later category changes and saved-draft opening. It adds a blank, non-removable
field for each missing required aspect, does not invent its value, and shows one required
instruction. A value entered in that field is seller-provided. The server still blocks
an unknown name from a stale or modified request even when Trading might accept a custom
seller name.

Each item specific stores an array of values. A single-value aspect has one array item,
and a multi-value aspect has up to 30 items. Trading writes each item as a separate
`Value` element under one `NameValueList`. Existing drafts that contain one `value`
string convert when they load and save the array shape on the next edit. Publication
also enforces 45 names and the standard 65-character value limit when Taxonomy does
not return a different maximum. A rule that the app cannot evaluate blocks publication
instead of being ignored. `VerifyAddFixedPriceItem` remains the final eBay check. The
app does not create or edit business policies.

Always call `VerifyAddFixedPriceItem` before `AddFixedPriceItem`. Use the saved draft
UUID for every repeat add request. Treat a duplicate-UUID response with the original
item ID as success. Network failures and responses without a confirmed item ID keep
the saved draft, listing snapshot, policies, and valid media references for a safe
retry. Save each Media API upload immediately so a retry of the same source draft
revision skips unexpired references. Discard those references when the source revision
changes, then upload the current ordered photo set.

An old ready or failed Seller Hub handoff requires one confirmation that no Seller Hub
draft exists. Remove that historical handoff after confirmation, then permit direct
publication. A completed historical transfer stays blocked.

After confirmed publication, save the eBay item ID, mark the listing live, and remove
the source from unfinished drafts. Seller Hub becomes the editing and management
surface. Existing retrieve, revise, end, and relist adapter methods remain unwired from
the browser for possible later listing management.

Keep direct publication behind `EBAY_DIRECT_PUBLISH_ENABLED=false` until the external
checks below pass. Keep all Trading XML in one replaceable adapter and use modern REST
APIs for Taxonomy, Metadata, Account, and Media support data.

The first supported listing shape is `EBAY_US`, USD, fixed price, quantity one, and no
variations. eBay is the source of truth for live state. Seller Hub remains the place
for orders, shipping, returns, messages, and payouts.

## Consequences

- Seller Hub is the editing and management path after the app publishes a listing.
- The app stores encrypted refresh tokens, API-neutral listing snapshots, eBay media
  references, handoff state, historical Feed task state, and live item mappings. It
  never stores photo bytes.
- Each eBay seller ID is unique across the two app accounts.
- Trading-created listings retain the stable app custom label as their SKU mapping.
- Account-deletion challenge and signed-notification handling is required before the
  first Production API call because the app stores eBay user data.
- Historical Feed status and CSV downloads remain available. New handoffs do not
  create Feed tasks or CSV files. No queue, worker, object store, or paid service is
  added.
- Existing listings are not imported until new app-created listings work reliably.
- Inventory API adoption needs a later accepted decision and measured need.

## Amendment — 2026-09-01

Decision 0008 supersedes source-draft removal after publication. Keep the source draft,
mark it published and read-only, and preserve its browser-local photos until the user
deletes the draft. Seller Hub remains the only editing surface for the live listing.

## External checks before live publication

Before normal Production publication, check eBay's current
[API deprecation status](https://developer.ebay.com/develop/get-started/api-deprecation-status),
run `VerifyAddFixedPriceItem` for one representative apparel listing, then publish one
low-risk Production listing. Confirm every field and ordered photo in Seller Hub, edit
one field there, and confirm that the edit remains. End the listing if it exists only
for validation. Then enable the flag and publish one normal listing for each household
account. Mock tests cannot satisfy these checks.
