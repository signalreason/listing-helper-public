# Set up your own copy

This guide is for the person who will operate the app. You need to configure service
accounts once. Sellers then use the browser. If you already have an app address and
password, go to the [reseller guide](user-guide.md).

## Before you start

Have these accounts and tools ready:

- GitHub, to hold your copy of the source code.
- DigitalOcean with billing enabled, to run the app and its PostgreSQL database.
- An OpenAI API project with billing and permission to set a hard spending limit.
- An [eBay Developers Program account](https://developer.ebay.com/), with application
  keys for the environment you will use. An ordinary seller login is not a developer keyset.
- An eBay seller account with shipping, payment, and return business policies.
- A password manager for keys and generated passwords.

The supported setup uses one DigitalOcean App Platform web service and one PostgreSQL
database. It does not require you to build a server or maintain a container system.
For local development, see the [developer guide](development.md).

## Cost and storage

Example checked on September 8, 2026:

| Resource | Example monthly amount |
| --- | --- |
| One 512 MiB web service (`apps-s-1vcpu-0.5gb`) | $5 |
| One 512 MB development PostgreSQL database | $7 |
| OpenAI usage | Metered; set an $8 hard limit if this suits your budget |

These amounts exclude applicable taxes, a custom domain, excess bandwidth, and eBay
fees. Hosting prices and usage can change. Confirm the total shown before creating
resources. See [DigitalOcean pricing](https://docs.digitalocean.com/products/app-platform/details/pricing/).

The inexpensive development database has no automatic backup or high-availability
protection. Loss of the database can require account recreation, eBay reconnection,
and recreation of drafts. Choose this setup only if that loss is acceptable. Review
[DigitalOcean database options](https://docs.digitalocean.com/products/app-platform/how-to/manage-databases/)
if you need stronger protection. Keep photo originals separately in all cases.

In your OpenAI project's **Limits**, set the monthly amount and enable **Enforce a hard
limit**. Also set an earlier spend alert. An alert alone does not stop requests. A hard
limit can allow a small overrun while enforcement takes effect. See
[OpenAI spend limits](https://developers.openai.com/api/docs/guides/spend-limits).
The app does not set or confirm your provider billing limit.

## 1. Prepare the source and hosting

1. Create your own GitHub copy of this repository. Keep deployment values out of Git.
2. Open DigitalOcean App Platform and start creating an app from that repository.
   Authorize the DigitalOcean GitHub App to read your copy.
3. Use [the example app specification](../.do/app.yaml) for the service and database
   settings. Replace `YOUR_GITHUB_OWNER/YOUR_REPOSITORY` with your copy's repository
   name in the App Platform configuration. The CLI alternative is in the
   [deployment reference](../.do/README.md).
4. Use one web service, one worker process, and the `accounts-db` PostgreSQL database.
   Keep the `DATABASE_URL` binding from the example. `bin/start` applies migrations
   before it starts the service.
5. Keep automatic deployment disabled while you set up and review changes. No web
   domain from another installation is included in the example.
6. Add the runtime values below to the **web** component. Use encrypted values and
   **Run time** scope for credentials. Never put credentials in build settings.

## 2. Set the runtime values

Generate random values in your password manager, or use the commands in the deployment
reference. Store them there so you can recover the installation. Do not paste values
into GitHub issues, screenshots, or command arguments.

| Setting | What to enter |
| --- | --- |
| `SESSION_SECRET` | A new random secret with at least 48 random bytes |
| `APP_SETUP_TOKEN` | A separate random secret with at least 32 random bytes; remove after first setup |
| `OPENAI_API_KEY` | A key from the OpenAI project whose billing limit you configured |
| `APP_OPERATOR_NAME` | The name to show publicly on this installation's privacy page |
| `APP_PRIVACY_EMAIL` | A valid contact email to show publicly on the privacy page |
| `EBAY_CLIENT_ID` | The selected eBay keyset's App ID / Client ID |
| `EBAY_CLIENT_SECRET` | The same keyset's Cert ID / Client Secret |
| `EBAY_RUNAME` | The redirect URL name issued by eBay; this is not the callback URL |
| `EBAY_TOKEN_ENCRYPTION_KEY` | A Fernet key; generate once using the deployment reference |
| `EBAY_ITEM_POSTAL_CODE` | The item location shared by the two sellers |
| `EBAY_NOTIFICATION_VERIFICATION_TOKEN` | A random 64-character hexadecimal value |
| `EBAY_NOTIFICATION_ENDPOINT_URL` | Your exact HTTPS address followed by `/api/ebay/account-deletion` |

The example already sets `OPENAI_MODEL=gpt-5.5`, `EBAY_ENVIRONMENT=sandbox`, and
`EBAY_DIRECT_PUBLISH_ENABLED=false`. Use matching Sandbox credentials initially.
Use `SESSION_COOKIE_SECURE=true` for HTTPS (the default).

The two privacy settings are intentionally public in the running app. Choose contact
details you want its users to see. Their real values still do not belong in source code.
Without both valid settings, `/privacy` returns a setup error instead of a policy.
Review the supplied policy against your actual data practices before use. If you change
service providers or retention behavior, update the policy too.

## 3. Choose a stable HTTPS address

Deploy the app to obtain an HTTPS address. You can use its DigitalOcean address or add
a custom domain you control. Choose the final address before registering eBay URLs.
In the examples below, replace `https://listing.example.com` with that address.

Open these pages:

- `https://listing.example.com/healthz` should return `{"status":"ok"}`.
- `https://listing.example.com/privacy` should show your operator name and contact.
- `https://listing.example.com/login` should show first-owner setup while no account exists.

If deployment fails, check that the database binding and runtime settings exist. Never
share a full environment or connection string when asking for help.

## 4. Create the owner account

1. Open the login page and enter your email and the temporary setup token.
2. Store the generated password in your password manager. It is shown once.
3. Continue to the app. Remove `APP_SETUP_TOKEN` from the web component and let the
   setting change deploy.
4. If needed, use **Users** to create the second account. Give its password to that
   seller through a private channel. The app does not send password emails.

There are at most two active accounts. There is no public signup or password-reset email.
The owner can reset the second user's password. Keep the owner's password safe.

## 5. Configure eBay and connect a seller

In the eBay developer portal, configure OAuth for the chosen keyset:

| Purpose | Example value |
| --- | --- |
| Accepted callback URL | `https://listing.example.com/api/ebay/oauth/callback` |
| Privacy policy URL | `https://listing.example.com/privacy` |
| Account-deletion endpoint | `https://listing.example.com/api/ebay/account-deletion` |

Use OAuth, and copy the resulting RuName into `EBAY_RUNAME`. Keep Sandbox and Production
keys, RuNames, and seller accounts separate. The app handles access and refresh tokens;
you do not need to generate a seller token manually. See [eBay authorization](https://developer.ebay.com/develop/guides/sell/authorization).

This app stores eBay account data, so configure Marketplace Account Deletion
notifications. Set exactly the same endpoint and verification token in the app and
portal. Complete the GET challenge, then test the signed POST. A successful GET alone
does not prove deletion works. See [eBay account-deletion setup](https://developer.ebay.com/develop/guides/sell/marketplace-user-account-deletion).

Sign in to the app, select **Connect eBay**, and approve access with the matching seller
account. Each app account can connect one different seller. Confirm that category menus
load. Category-based generation needs the eBay application configuration even when you
plan to copy the result rather than publish it.

## 6. Review the first listing

1. Add photos of an ordinary apparel item. Choose its category before generation.
2. Generate a draft and check each field against the item. AI-generated facts and prices
   can be wrong. Add measurements and flaws that photos cannot establish.
3. Save the draft, reopen it in the same browser, and confirm the photos are present.
4. For publication, select existing shipping, payment, and return policies. Enter the
   package weight and dimensions, price, category, and condition.

Live publication stays disabled in the example. The project's Production and maximum-load
acceptance checks are still open. Sandbox results do not prove Production behavior.
For a controlled Production check, use Production keys and the matching RuName, set
`EBAY_ENVIRONMENT=production`, reconnect the seller, and complete notification setup.
Temporarily enable `EBAY_DIRECT_PUBLISH_ENABLED` only for the planned check. Publishing
creates a real listing and can incur eBay fees. Verify every field and ordered photo in
Seller Hub, edit one field there, and end the listing if it was created only for testing.
Keep normal publication disabled until the checks in
[decision 0006](decisions/0006-seller-hub-drafts-and-trading-adapter.md#external-checks-before-live-publication)
and the [production work package](ebay-listing-assistant-plan.md#103-wp-07-production-readiness-current-work-package)
are complete.

## Updates and recovery

Review release notes and tests before deploying a new revision. See the
[deployment reference](../.do/README.md) for safe updates. Keep original photos and a
secure copy of the encryption key. Replacing that key without a migration makes stored
eBay authorization unreadable. A lost database has different recovery needs from a lost
browser photo copy; see the [reseller guide](user-guide.md#when-something-goes-wrong).
