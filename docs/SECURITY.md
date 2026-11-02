# Security and privacy boundaries

## Deployment model

Grounded is a single-operator desktop application with a loopback backend. A second
Mac connects through an SSH tunnel. The API is not designed for public exposure or
multi-tenant authentication. Origin/custom-header checks limit browser-originated
requests; they are not credentials against another local process.

## Data ownership

- The desktop collects audio only after explicit meeting selection and authorization.
  System playback can include other applications. Channel labels do not identify people.
- Pending audio is encrypted on the participant device with AES-GCM and an OS-backed
  wrapped key. It remains until a durable terminal outcome or explicit deletion.
  Unavailable key storage blocks capture; no plaintext fallback is used.
- The backend stores transcripts, claims, findings and short quotations in private
  SQLite. File permissions restrict access; the database is not claimed to be encrypted.
- Runpod receives audio/model inputs and Firecrawl receives verification queries.
  Provider retention policies apply. Local memory-only worker handling is not a
  promise of zero provider retention.

## Trust boundaries

The renderer uses sandboxing, context isolation and a restricted preload bridge.
Main-process commands validate frame origin, command shape and permitted destinations.
Provider credentials are loaded only by the backend from private configuration files;
they are not sent to the renderer or packaged in the app.

Meeting text and fetched pages are untrusted data. Models do not receive arbitrary
local execution tools. Structured outputs are validated against supplied identities,
and citation text must exist in retrieved evidence. Prompt-injection resistance and
semantic correctness are not guaranteed by these checks.

## Operational safeguards

Queues, request sizes, retries, storage and response reads are bounded. Diagnostics
exclude conversations, audio, raw provider bodies and credentials, with private
permissions and rotation. Stable audio identities and transactional receipts make
database effects idempotent; cloud inference may repeat after an ambiguous crash.

Use normal macOS authorization. Do not disable certificate checks, remove quarantine
attributes, reset Keychain material or expose the API to make a demonstration work.
The development package is ad-hoc signed, not notarized. Synthetic secrets/artifact
scanning is available through `scripts.audit_submission`; it is not a penetration
test or a proof that dependencies contain no vulnerabilities.
