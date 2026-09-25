# Packet P4-T4: webhooks, certificates, signed judge records, embeddable gallery (T4 complete)

Starts after G3. Bulk import/export and the REST API already exist (P2-RX, P3-OPS); this packet completes the remaining T4 bullets. Read AGENTS.md, BUILD-SPEC 2, 16, 17. New dependency approved for this packet only: `cryptography` (Ed25519).

## 1. Webhooks ("covering every action the UI can take")
- Every `audit.record` action is also a webhook event type (single taxonomy: `project.submitted`, `review.submitted`, `results.published`, `ballot.cast`, ...). `WebhookEndpoint` (event FK, url, secret, `event_types` JSON list or ["*"], `is_active`, created_by) and `WebhookDelivery` (endpoint, event_type, payload JSON, attempt, status_code, response_ms, error, next_attempt_at, delivered_at).
- Delivery after commit (`transaction.on_commit`) by a small in-process worker thread started with gunicorn workers (or a `manage.py deliver_webhooks` loop run by runportal in a thread): POST JSON, headers `X-Verdict-Event`, `X-Verdict-Delivery`, `X-Verdict-Signature: sha256=<hmac(secret, body)>`, 5 s timeout, retries with backoff (1 m, 5 m, 30 m, 2 h, then failed). Failures never roll back or block the user action.
- SSRF guard: http/https only; resolve the host and refuse loopback, link-local, private and metadata ranges unless `WEBHOOKS_ALLOW_PRIVATE=1` (needed for offline demos with a local receiver; documented).
- Payloads use the same public/organizer projections as the API (never peer scores to non-organizers; webhook endpoints are organizer-configured so they receive organizer projections, documented).
- Organizer page `/manage/{slug}/webhooks`: add/test/disable endpoints, delivery log with replay. `scripts/webhook_receiver.py` (stdlib) prints and verifies signatures for demos.

## 2. Certificates and records
- `/events/{slug}/certificates/{kind}/{public_id}` printable HTML (print CSS, A4 landscape): participation (team members of submitted projects), judge participation (judges with ≥1 submitted review), winner (per published result/prize). Visible to the person it names and organizers; includes a verification URL + code.
- Organizer page lists all certificates with bulk "print all" and a CSV of verification codes.

## 3. Signed, publicly verifiable judge participation records
- Instance Ed25519 key pair generated on first boot, private key stored in `/data/keys/` (0600), public keys published at `/.well-known/verdict-keys.json` with `kid` and creation time (rotation keeps old public keys).
- Record = canonical JSON {kid, record_id, event {slug, name}, judge {display_name, public_id}, reviews_submitted, tracks, issued_at} (no scores, no project-level detail), signature base64url. `GET /records/{record_id}` (public JSON + human page), `POST /api/v1/records/verify` (paste JSON → valid/invalid + reason), `/verify` page. Issued by organizers after judging closes (bulk), revocable (revocation list in keys document).
- Offline verification script `scripts/verify_record.py` using only the published public key (document that it needs `cryptography` or any Ed25519 tool).

## 4. Embeddable gallery widget
- `/embed/{slug}` minimal, fast public gallery view (cards, search box, track filter) with CSP `frame-ancestors *` (only this route) and no cookies set; snippet `<iframe src=".../embed/{slug}" ...>` + optional `/embed/{slug}.js` that injects the iframe and auto-resizes via postMessage. Organizer page shows the snippet and a live preview.

## Tests (`tests/test_t4.py`)
Webhook signature correctness, retries/backoff, SSRF refusal, delivery never blocks the action, organizer-only management; certificate access (owner/organizer yes, others 403), verification codes; record signature verifies with the published key, tampering fails, revocation reported, record contains no scores; embed route has frame-ancestors * while every other route keeps 'none', and shows only public projects.

## Files you own
src/interop/{webhooks.py,signing.py,certificates.py} (+ models/migration in interop), templates for these pages, src/static/js/embed.js, scripts/webhook_receiver.py, scripts/verify_record.py, tests/test_t4.py, requirements.txt (add cryptography only).
