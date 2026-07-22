# SFU node identity and trust boundary

## Scope and authority

The Hub is the only authority for SFU runtime identity state. The SQL repository stores identity, role, public credential fingerprints, rotation overlap, revocation state, version fences, idempotency receipts, and enrollment rate buckets. No Worker may enroll or orchestrate another Worker, and there is no authoritative in-memory fallback.

The default configuration remains `observe_only`: `activation.enabled` is false and no evidence identifier is present. Runtime authorization must remain disabled until the release process supplies verified, existing `SRC_*` or `RUN_*` evidence. File paths and URLs are not substitutes for those identifiers.

## Selected stock LiveKit boundary

The repository decision in `docs/decisions/ADR-livekit-broadcast-runtime-boundary.md` selects `livekit_control_api`. In this mode:

- The Hub calls the real LiveKit server API over verified TLS with a separately managed API key and secret.
- The identity database receives only `sha256:<hex>` credential fingerprints. It has no field for the API secret.
- A node-owned enrollment key signs the canonical enrollment statement. That private key is generated and retained outside the Hub.
- API grant provisioning must issue distinct credentials for `sfu_control` and `sfu_observer`.
- This mode does not claim or emulate node mTLS. A fingerprint lookup is a local Hub policy check, not a replacement for LiveKit API authentication.

## Optional authenticated runtime extension

`authenticated_runtime_extension` is a separate, explicit mode. It requires a trusted CA bundle and strict client mTLS at the transport terminator. A certificate is accepted only when all of these checks pass on every authorization attempt:

- the TLS layer reports peer verification success;
- the certificate chains directly to a configured CA;
- current time is inside the certificate validity interval;
- the sole URI SAN is `spiffe://ananta.local/sfu/<role>/<node-id>`;
- EKU contains TLS client authentication;
- the certificate public key equals the externally generated enrollment public key;
- the certificate fingerprint is absent from configured and persistent revocation state;
- the persistent identity role equals the required role.

Forwarded certificate headers from an untrusted proxy are not accepted by this service. TLS termination must pass a trusted transport-verification result through an internal application adapter; no public endpoint exposes that adapter.

## Enrollment protocol

Admin endpoints are:

- `POST /api/admin/webrtc/sfu-nodes/enroll`
- `POST /api/admin/webrtc/sfu-nodes/<node-id>/rotate`
- `POST /api/admin/webrtc/sfu-nodes/<node-id>/revoke`
- `GET /api/admin/webrtc/sfu-nodes/<node-id>`

Every mutation requires Admin RBAC, `Idempotency-Key`, an actor equal to the authenticated principal, a reason, and `expected_version`. Enrollment is rate-limited by a persistent per-actor/source/window bucket shared by all Hub instances.

Enrollment and rotation accept a public key plus a proof object. The proof signs the canonical `ananta.sfu-node-proof-of-possession.v1` statement, including operation, node, mode, one role, both public fingerprints, nonce, timestamp, expected version, actor, reason, and an idempotency-key digest. Nonces are persisted by digest and cannot be reused. Supported proof algorithms are Ed25519, Ed448, ECDSA-SHA256, and RSA-PSS-SHA256.

Requests containing private-key fields, API/client secrets, or PEM private-key markers are rejected before policy execution. Responses, receipts, and audit events contain metadata and fingerprints only. CSR creation and key providers remain outside this Hub boundary.

## Roles, rotation, and revocation

Each identity has exactly one role:

- `sfu_control` permits the control boundary only.
- `sfu_observer` permits observation only and is never accepted for control, enrollment, or admin authorization.

Rotation is an optimistic CAS mutation. The old credential enters `overlap` for the configured window, the new credential becomes active, and an older overlap credential is revoked. Emergency revocation atomically revokes every credential and is effective for fresh admission checks immediately. `revocation_deadline_at` records the configured maximum propagation SLO; because authorization reads durable state without a process cache, new Hub-side admission is denied as soon as the revoke commits and therefore no later than that deadline.

## Operational requirements

- Put the LiveKit API secret or runtime private key in a dedicated secret manager or mounted secret, never in enrollment JSON, application logs, or this table set.
- Configure HTTPS certificate verification for LiveKit; never set an insecure TLS option.
- For extension mode, mount read-only CA certificates and terminate mTLS only at a trusted internal boundary.
- Alert on denied proof, role mismatch, rate limiting, stale CAS, certificate rejection, and revocation deadline violations.
- Do not enable `activation.enabled` merely because enrollment succeeds. Release gates and valid source/run evidence remain independent prerequisites.
