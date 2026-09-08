# Deployment reference

Start with the [setup guide](../docs/setup.md). This page is for an operator who uses
the DigitalOcean CLI or maintains an existing installation.

## New installation

[app.yaml](app.yaml) is a template for one web service and one PostgreSQL database.
It has no operator identity, domain, or credentials. Automatic deployment is disabled.
Make an ignored local copy and edit its repository source and, if needed, domain:

```sh
cp .do/app.yaml .do/app.local.yaml
doctl apps create --spec .do/app.local.yaml
```

Do not run the command until you have replaced `YOUR_GITHUB_OWNER/YOUR_REPOSITORY` and
reviewed the billed resources. Add credentials through the App Platform web component's
encrypted **Run time** settings. Use the database binding in the template.

## Generate values

Run these in a private local terminal and store the results in your password manager.
Never copy the output into Git, reports, logs, or screenshots.

```sh
# SESSION_SECRET
openssl rand -base64 48
# APP_SETUP_TOKEN (remove from the service after first-owner setup)
openssl rand -base64 32
# EBAY_NOTIFICATION_VERIFICATION_TOKEN: 64 hexadecimal characters
openssl rand -hex 32
# EBAY_TOKEN_ENCRYPTION_KEY, after uv sync --locked
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

The [runtime settings table](../docs/setup.md#2-set-the-runtime-values) lists every
required value. `APP_OPERATOR_NAME` and `APP_PRIVACY_EMAIL` are public policy contact
settings, not credentials. They must describe the operator of this installation.

## Existing installation: privacy settings migration

Before deploying the public-preparation changes, set `APP_OPERATOR_NAME` and
`APP_PRIVACY_EMAIL` on the existing web component. Use the same public operator name
and contact that its current privacy page shows. Deploy the settings first. The older
code ignores these new settings. Then deploy the code and check `/privacy` on a phone.
Without valid settings, the new page returns HTTP 503. Do not remove a working contact
or register an incomplete policy with eBay.

## Update safely

Do not apply the example specification over an existing application. App specification
updates replace configuration and can remove domains, credentials, or runtime overrides.
Use App Platform's settings to change only the intended fields, preserving the existing
source, domain, database binding, and encrypted runtime values.

Use a reviewed commit from your repository. If you enable automatic deployment,
merging to the configured branch can deploy immediately. Migrations run on startup.
After deployment, confirm the active source commit and health. Do not claim completion
from a pushed task branch alone when the task includes deployment.

```sh
doctl apps get-deployment APP_ID DEPLOYMENT_ID --output json   | jq '.[0] | {id, phase, source_commits: [.services[] | {name, source_commit_hash}]}'
curl -fsS https://listing.example.com/healthz
```

Replace the IDs and example hostname with your own values. Do not print the complete
application specification or runtime environment. Keep publication disabled for normal
use until the [external checks](../docs/decisions/0006-seller-hub-drafts-and-trading-adapter.md#external-checks-before-live-publication)
pass. Do not change the eBay environment without matching keys and seller authorization.
