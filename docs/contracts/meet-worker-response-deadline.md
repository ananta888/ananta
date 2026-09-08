# Bounded Hub reads of successful media-Worker responses (MAP-11/29)

## Source audit before implementation

At `76ec57d71`, dialog-start and signed capability-failure responses already
use the shared bounded HTTP reader. Successful media turns still use one
`response.read(MAX_RESULT_BYTES + 1)` with a socket timeout. A peer that keeps
trickling bytes can keep that read active past the turn's original deadline;
a socket inactivity timeout is not an absolute body-read budget. Signature
validation happens only after the read, so it cannot bound an unfinished body.

Reproduce with an owned real HTTP server, a valid signed synthetic response
and a slow body. Keep an independent test deadline and exact cleanup. Then
reuse the existing one-underlying-read-at-a-time reader in the Hub media
transport, with a monotonic deadline derived once from the remaining original
turn budget. Preserve the same signature, size/publication/profile checks,
no-redirect/no-proxy/private-address policy and closed error codes. Never retry
or admit a result that was read after its body deadline.

Preserve the existing `meet_worker_result_too_large` response for overflow;
deadline/partial-read failures remain bounded unavailable results. Verify fast
valid responses, replay/signature rejection, overflow and cleanup alongside
the trickle case. The reader still depends on the transport's finite socket
timeout for any one stalled underlying read, connect or response headers;
do not advertise a new whole-request hard real-time guarantee. This change
removes indefinite trickle extension, not all network timing uncertainty.

The reusable reader remains in its historical `persona_http` module (preserved
naming/coupling debt). Reuse that small IO seam rather than introducing another
reader or mixing task/governance decisions into transport (SRP/DIP). No Worker
authority, protocol version, database or running service change is planned.
