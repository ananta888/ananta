# SFU Fanout Route State Machine

## Authority boundary

The Hub is the sole owner of route intent, epochs, fencing, retries, state and
reconciliation. Runtime adapters execute one already-authorized absolute
projection. They do not evaluate policy, expand an audience, mint an epoch or
delegate to another worker.

## States and transitions

| Current | Event | Next | Required guard |
| --- | --- | --- | --- |
| `intent` | `dispatch` | `dispatch` | persisted intent, route not expired |
| `dispatch` | `ack` | `ack` | mode-specific, fully bound apply evidence |
| `ack` | `activate` | `active` | current epochs and fence, active parent subscription |
| `active` | `update` | `update` | persisted successor intent |
| `update` | `ack` | `ack` | mode-specific, fully bound update evidence |
| any nonterminal | `revoke` | `revoke` | persisted Hub command |
| any nonterminal | `expire` | `expire` | Hub clock reaches bounded expiry |
| any nonterminal | `fail` | `failed` | explicit reason-coded Hub decision |

`revoke`, `expire` and `failed` are terminal. Unknown transitions deny with
`route_transition_forbidden`. Repeated operation IDs with the same request
digest are idempotent; reuse with different content denies with
`route_operation_conflict`.

## Apply evidence

All accepted evidence binds the operation and idempotency IDs, nonce, monotone
sequence, projection version, expiry, tenant, room, runtime scope, intent
digest, fencing token, route epoch and runtime control mode.

For `livekit_control_api`, activation additionally requires a TLS- and API
credential-bound server response followed by reconciliation. Stock LiveKit is
not represented as returning a node signature or enforcing the Hub fence.

For `authenticated_runtime_extension`, the runtime acknowledgement must have
been verified through mTLS or an external signature verifier. The state
machine consumes only the verification result and never creates a signature or
source identifier.

## Deadline, retry and cooldown

Dispatch and update have separate bounded deadlines. Timeout before the
deadline is rejected. A due timeout consumes one retry, enters a bounded
cooldown and requires an explicit Hub retry. Exhausting the retry budget moves
the route to `failed`. Route expiry preempts dispatch, acknowledgement and
update.

## Stable audit boundary

Every attempted transition returns a canonical audit digest, including denied,
duplicate, reordered, late and incorrectly bound responses. The caller owns
durable audit persistence. The lifecycle service has no Flask, SQL, SDK,
network or worker-orchestration dependency.

## SOLID check

- SRP: policy compilation, grouping, persistence and runtime lifecycle remain
  separate services.
- OCP and DIP: runtime modes are verified behind explicit evidence data; new
  adapters do not change Hub policy.
- LSP: unsupported or unverified runtime results deny rather than simulate an
  acknowledgement.
- ISP: the state machine consumes only transition guards and apply evidence.
- Hidden side effects: transitions are pure values; persistence and dispatch
  remain explicit caller responsibilities.
