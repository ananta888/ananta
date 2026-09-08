# Machine admission acceptance (MAP-06)

Source audit: Ananta `6a81636d9`, Meet `b0d6c10` (including upstream
`84da270`). This is a contract/verification audit of the implemented admission
path, not a new trust enablement, deployment or production-release approval.

| Criterion | Implemented boundary | Verification |
| --- | --- | --- |
| Exact machine principal, room, task, runtime, capabilities and expiry | `MachineAdmission` accepts the closed signed v2 fields under the configured issuer/key/audience/scope profile; `MachineSessionLeases` retains their immutable tuple and device fingerprint. | `machine-admission`, `machine-trust-admission`, `machine-session-leases`; root `test_meet_machine_principal_interop` supplies actual Hub role-derived signatures. |
| Fresh proof and no human ticket upgrade | The machine route independently checks the exact normalized join fields through P-256 proof, requires the supported receive profile and required SFrame, then verifies the machine grant. HTTP origin, WebSocket origin and one-use ticket checks remain shared. | `device-proof`, `session-tickets`, `machine-admission`, `machine-trust-http`. |
| Automatic, single-use credentials and negative scope cases | A validated machine admission issues a short-lived one-use session ticket. Grant nonces are consumed across trust rotation keys; stale/foreign room, task, tenant, project, subject or device cannot renew another lease. | Real HTTP/WebSocket tests include three renewals with one membership, foreign-subject observation/renewal, replay, wrong issuer/audience/key and exact scope tuples. |
| Unchanged room, pair and human boundaries | Machine sockets use the ordinary `RoomRegistry.join` capacity path. Machine admission rejects pair mode; names are fixed to the KI label, not an identity source. Human OIDC remains independent. | `room-registry` covers 20 peers, pair two-device cap and room isolation; `machine-admission` covers human auth and denied pair/name changes. |

No new scheduler, worker authority, media generation, private key export or
permission inference is present in this path. Verification should use existing
real cryptographic/HTTP/SQL tests plus the current companion full regression;
do not add a duplicate implementation solely because the earlier TODO still
lists this contract as partial.

The legacy v1 compatibility path remains deliberately narrower than v2: it
does not claim v2 runtime/session/capability projection. The v2 path supplies
the MAP-06 contract. Provisioning orchestration and operator rollout (MAP-05),
general crash recovery (MAP-11), decoder startup reliability (MAP-08/29) and
public infrastructure acceptance (MAP-31) remain separately tracked.

SOLID review: cryptographic trust, admission projection, device verification,
lease lifecycle and ticket consumption are separate responsibilities with
closed interfaces. The broad existing HTTP composition in `src/server.js`
remains SRP debt; this audit does not add another concern to it. A future
route extraction should preserve those existing adapters and exact order of
verification rather than combine them into one authentication service.

## Closure verification

The complete isolated companion regression at `59ce395` passed with 665
frontend and 759 Node tests, zero failures, build/security and Go unit/vet
green (242.353 s Node stage). It includes the actual P-256/HTTP/WebSocket
admission, three-renewal, trust-scope/replay, ticket, human and room/pair tests
mapped above. Results are pushed in `1a62de0`. Root's actual SQL/Hub-signature/
Meet cross-subject interoperability case also passed in the 208-case media
regression. Its two unrelated bootstrap teardown failures were corrected and
the affected 40-case regression passed without errors in 31.60 s.

MAP-06's four v2 admission criteria are therefore complete. No duplicate
authentication implementation was needed. External trust provisioning, public
TURN, general reconnect recovery and source-delivery quality are not promoted
by this closure; neither synthetic test credentials nor this source audit are
production release evidence.
