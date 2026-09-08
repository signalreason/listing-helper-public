# 0005 — Two-user access and load protection

- Status: Accepted; storage consequence amended by decisions 0006 and 0007
- Date: 2026-08-05

## Context

The application is a production tool for two trusted people in one household. It is
not an invite-code pilot, an enterprise product, or a public SaaS. It needs enough
access control to keep strangers from spending the OpenAI budget or consuming the
small server, without email delivery, OAuth, MFA, organizations, or an elaborate
permission system.

At the stretch workload of 10 products in the busiest one-hour window, the expected
generation rate is 10 per hour. A limit must leave that workload below 50% utilization.

## Decision

Use individual email/password accounts backed by the App Platform PostgreSQL database.
Support at most two active accounts. One account is the owner and can create, disable,
or reset the other account from a small owner-only user page.

Creating or resetting a user takes an email address and generates a cryptographically
random 20-character password from an unambiguous alphabet. Store only an Argon2id
password hash. Return the plaintext password only in the successful create/reset
response, never log or persist it, and show it once with a copy button. Do not send
email. A reset replaces the old password immediately.

Bootstrap the first owner through a one-time setup page that is available only while
no users exist and requires a temporary `APP_SETUP_TOKEN` encrypted runtime secret.
Remove that environment variable after setup. Login creates a signed 30-day session
cookie containing only the user ID and password version. Set `Secure`, `HttpOnly`, and
`SameSite=Lax`; sign it with a separate `SESSION_SECRET` encrypted runtime variable;
and verify the current password version on authenticated requests. Mutating requests
require same-origin protection.

Use `psycopg` with small versioned SQL migrations rather than an ORM, `argon2-cffi` for
password hashing, and `itsdangerous` for signed session cookies. These are the only new
account-layer libraries in the baseline.

Use these intentionally generous generation controls:

- 24 successful generation starts per rolling hour per account;
- 30 successful generation starts per rolling day per account;
- one in-flight generation per account; and
- two in-flight generations for the application.

At 10 products in one hour, hourly utilization is `10 / 24 = 41.7%`; at 10 products
in one day, daily utilization is `10 / 30 = 33.3%`. Rate-limit only authenticated
generation starts; validation failures do not consume the quota. Use a short,
plain-language retry message when a limit or concurrency guard is reached.

Limit failed logins separately to 10 attempts per 15 minutes for an email-and-IP pair
and 30 attempts per hour per IP. In-memory counters are sufficient for the initial
single process; resets during deploys are acceptable for this trusted, two-user app.

## Consequences

- Users get a normal login and never need a shared invite code.
- The owner can add or recover the second account without an email service or shell
  access, and can copy the generated password directly.
- The application stores account data but does not store photos. Accepted
  [decision 0006](0006-seller-hub-drafts-and-trading-adapter.md) and
  [decision 0007](0007-save-unfinished-listing-drafts.md) later added the unfinished
  draft and eBay recovery records to the same PostgreSQL service.
- Password reset increments the password version, invalidating existing sessions for
  that account without a server-side session table.
- The $7 database reduces the monthly OpenAI hard limit from $15 to $8 under the
  existing $20 total budget.
- There is no self-service signup, password recovery email, email verification, MFA,
  audit trail, team model, or public registration. Add one only when actual expansion
  requires it.
- A development database is not a high-availability or backed-up production database.
  That risk is accepted for two easily recreated accounts and should be revisited
  before serving outside users.
