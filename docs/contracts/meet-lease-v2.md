# Request-bound Meet lease callbacks

The previous response signature covered only the JSON result (`allowed`). A
recorded correctly signed allowance could therefore be replayed for another poll
or task. V2 closes that replay path without granting workers renewal authority.

The callback path remains `/api/meet/v1/internal/lease`. The closed request now
contains `task_id`, `lease_id`, and a fresh 32-character lowercase hexadecimal
`nonce` generated for **every** poll. The nonce is a cryptographic challenge, not
an SRC/RUN identity or permission. Existing request HMAC authentication remains.

The Hub responds with `X-Ananta-Lease-Protocol: ananta.meet-lease.v2` and signs:

`ASCII("meet-lease-v2") + NUL + SHA256(exact_request_bytes) + exact_response_bytes`

The worker requires that protocol and exact request-bound response signature. It
never falls back to V1. The Hub rejects authenticated legacy two-field requests
with 409 `meet_lease_protocol_upgrade_required`, before consulting lease authority.
No insecure compatibility switch is provided. User-facing turn/association
routes and the Hub-to-worker image assignment remain unchanged.

## Upgrade and budgets

Upgrade the authorizing Hub and its media workers together during a bounded
publication pause. An old/new mismatch fails closed; do not disable signature
checks to keep an old publisher alive. The origin and callback URL do not change.
The isolated worker image must be rebuilt even though the task envelope is the
same. This security migration deliberately does not preserve replayable V1
allowances.

The guard uses the original turn deadline, a maximum 3-second socket timeout and
an absolute bounded-body read budget, caps responses at 512 bytes, rejects proxy
and redirect paths, and checks that `allowed` is exactly true with no extra
fields. Outer worker execution limits remain necessary for the whole operation,
including network setup and response-header reads. The worker cannot extend the
authoritative Hub task lease by creating another local guard.

Tests exercise actual headless HTTP revocation, replay across polls/tasks/leases,
validly signed V1 downgrade, malformed challenges and already expired turns.
They use synthetic keys; they are not production release evidence.
