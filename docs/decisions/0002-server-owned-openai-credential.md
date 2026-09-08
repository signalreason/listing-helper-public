# 0002 — Server-owned OpenAI credential

- Status: Accepted
- Date: 2026-08-05

## Context

Requiring users to subscribe to ChatGPT, install Codex, create an OpenAI developer
account, or supply an API key adds setup and makes the product harder to use.

## Decision

The operator supplies one OpenAI API key as the server's `OPENAI_API_KEY` runtime
environment variable. The application reads that environment variable only in the
server request path when it needs to authenticate an outbound OpenAI request. It must
not copy the value into an application config file, database, cache, client bundle,
response, log, error, metric, trace, test fixture, or generated artifact.

On DigitalOcean App Platform, configure `OPENAI_API_KEY` as an encrypted, run-time-only
secret. The browser or any future desktop client calls only the application server. It
never receives the credential and never calls OpenAI directly. A missing environment
variable fails closed with a generic user-safe error.

## Consequences

- Users can generate a listing without any OpenAI account or credential.
- The operator owns API usage and cost.
- Public deployment requires the accepted individual-account and load controls in
  [decision 0005](0005-two-user-access-and-load-protection.md).
- Logs, errors, telemetry, tests, and client bundles must be checked for accidental
  secret exposure.
- Credential rotation must not require a client release.
- Tests must prove that request and error paths do not serialize the environment value.
