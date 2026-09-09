# Hub-controlled bounded dialog reconnect

MAP-11 source audit, 2026-09-09, Ananta `44016f299`, Meet `b11d749`.
Control reads already reconnect their HTTP transport with at most two retries,
100/200-ms backoff and the original 2.5-second freshness window. Three actual
lease renewals, Worker loss/stall and real Hub process restart are verified.
However, `require_dialog_session` intentionally terminates on membership loss;
`DialogSessionOperations` never retries an old grant. The persistent phase
record also rejects replacement membership. Room rejoin is not implemented.

## Required implementation boundaries

1. Add an authenticated Meet retirement receipt for one exact v2 session and
   immutable Hub binding. It only removes that session; it cannot admit a peer,
   extend a lease, select another room or issue receive consent. Unknown/already
   absent session IDs produce an idempotent absence receipt under the verified
   caller binding, never evidence of prior ownership. The Hub must already
   possess its separately validated original membership before using that
   receipt. A present foreign binding must fail without touching that session.
2. Keep recovery admission in the Hub with durable assignment-scoped state:
   remember current validated membership, consume a bounded recovery attempt
   atomically, retire the old membership and wait for cleanup before issuing
   one fresh grant. The original Task/dispatch/runtime/source rights and total
   deadline remain unchanged. No post-hoc replacement of the assignment or
   automatic replay of old audio/chat/transcripts.
3. Negotiate recovery explicitly and bind it to preauthorization. Default
   legacy sessions retain fail-closed behavior. Hub loss, uncertain retirement,
   revoked policy or exhausted attempts produce a bounded failure, not a new
   independent Worker retry loop. The Worker may execute only the exact
   Hub-authorized reconnect and refresh its resource watchdog from fresh Hub
   replies; media remains closed while reconnecting.
4. Keep ordinary phase transitions strict. Only the Hub's separately validated
   recovery transition may replace old membership and invalidate its source
   observations. After joining, validate the new actual Meet membership epoch
   and current source controls before recreating source pumps. Late grants,
   callbacks and completions cannot restore the old generation.
5. Verify scope substitution and idempotent retirement, durable recovery races
   and restart, unchanged deadline/watchdog bounds, and real packaged Worker
   connection loss/rejoin with a separate receiver. No task completion is
   claimed from an isolated primitive or a successful retry alone.

SRP/DIP: persistence, recovery policy, authenticated Meet transport and owned
browser lifecycle remain separate narrow ports. The broad existing dialog
composition and Meet HTTP dispatcher are preserved SRP debt; do not add a
second scheduler to either. Retirement receipts are control-plane facts, not
SRC/RUN release evidence. Initial tests use explicit synthetic policy only.

## Retirement transport implemented

Meet now confirms exact v2 session retirement only after synchronous removal
of its owned registry member. A failed detach retains task occupancy; unknown
IDs do not claim prior ownership. Ananta validates the closed receipt, exact
nonce/session/immutable binding, fixed TLS origin/path, bounded body and current
Hub authority on both sides of the call. It never retries an uncertain request
or treats the receipt as a new lease. `membership_binding` is the shared pure
identity projection, avoiding divergent validation between live membership and
retirement (SRP). Existing authorization/observation consumers stay compatible.

118 targeted Ananta checks passed in 49.03 seconds; 61 companion checks passed
in 0.832 seconds, including real signed HTTP/WebSocket detachment, replacement
admission and late old-ID replay. Logs `/tmp/ananta-meet-retirement-hub.log`
and `/tmp/ananta-meet-retirement-final.log`. These establish the transport
primitive, not automatic reconnect or MAP-11 completion. Durable recovery
admission, negotiated Worker execution and actual recovery acceptance remain.

## Durable recovery resource bounds

The next resource slice stores one row per original Hub Task, bound to the
immutable assignment digest and original deadline. It stores only validated
membership metadata, two retired session/peer pairs at most, an attempt
counter, phase and time bounds; no grant, transcript, PCM or keys. At most two
reconnect attempts may be consumed over the entire original assignment.
An attempt has 30 seconds total, including a four-second quarantine starting
only after confirmed Meet retirement. One grant handoff may be claimed after
that quarantine; an uncertain handoff cannot issue another grant. Current Hub
policy and the original deadline remain mandatory at every service boundary.

Only a fresh validated new session/peer and a later membership epoch can settle
the joining phase. Same-session lease refreshes remain monotonic; retired IDs,
late old observations and a changed assignment cannot overwrite current state.
Clock rollback and expired recovery windows are terminal and persisted, not
merely exceptions rolled back with their transaction. SQL row locking, not an
in-process mutex, serializes concurrent Hub requests. The resource itself has
no task-dispatch, grant-issuance or browser-execution authority.

The resource is implemented as `SqlDialogRecovery` with separate immutable
`RecoveryOwner` / `RecoveryMembership` models and closed persisted-state
validation. 59 focused tests passed in 31.68 seconds, including two actually
spawned Hub-side processes competing for one grant slot, independent-connection
attempt races, restart, three monotonic refreshes, two complete recovery-state
cycles, expiry/rollback persistence, malformed records and retired-ID replay.
An earlier 32-test slice passed in 20.87 seconds; these suites overlap. Logs:
`/tmp/ananta-meet-recovery-resource.log` and
`/tmp/ananta-meet-recovery-resource-final.log`. No grant, browser rejoin or media
execution is simulated by this repository test. Service/Worker integration
and real connection-loss acceptance remain required; MAP-11 stays unfinished.

## Negotiation and phase fencing

The additive assignment/context field `reconnect: true` is now closed and bound
to original preauthorization. Invalid flags fail instead of being coerced.
The current authority projection includes this flag. A negotiated membership
read without its configured recovery coordinator is rejected before issuing
a control grant or making an HTTP request; legacy tasks remain unchanged.
The reconnect callback carries only its exact session and bounded attempt.
Signed response validation retains the original origin/room/task deadline,
checks the next attempt and quarantine, and cannot accept unnegotiated output.

The recovery digest is a domain-separated immutable authority projection, not
an evidence ID or the preauthorization record's digest. It includes the actual
verified organization/role principal and all profile/identity ceilings. Mutable
source control values and selected asset references do not change that identity;
their negotiation presence does, and current controls are still independently
revalidated before use.

Only the separate Hub phase recovery transition can reset a joined/publishing
record to connecting after confirmed retirement. It discards old publication
observations and preserves up to two retired session/peer pairs. Normal phase
transitions stay strict, including a late previously validated old observation
arriving after the phase reset. The existing Task CAS/audit port records the
attempt without changing the original dispatch context or creating another
task. Broad existing phase-service scope validation was extracted for shared
use (SRP); source-policy or SQL orchestration was not added to HTTP routes.

98 wire/resource/legacy checks passed in 46.19 s, 66 binding/authority checks in
32.93 s, 69 transport/route guard checks in 33.78 s, and 127 native phase/CAS/
negotiation checks in 52.05 s. Suites overlap. Logs:
`/tmp/ananta-meet-reconnect-{contract,binding,guard,phases}.log`. These complete
contract and phase components, not runtime activation or automatic rejoin.

## Hub composition and signed Worker receipt

`ANANTA_MEET_DIALOG_RECONNECT=1` now opts new Hub assignments into recovery.
Default `0` preserves legacy fail-closed sessions; malformed configuration and
missing authority/retirement/issuer/phase ports fail before resource creation.
The ordinary Hub bootstrap shares the exact recovery coordinator between the
dialog service and validated Meet observations, and shares its speaker-floor
withdrawal port. Current Task/assignment policy is rechecked before and after
every retirement, phase update and grant handoff. A failed retirement or phase
CAS stays `retiring`; a failed/uncertain grant handoff consumes its one slot
without issuing a replacement. No existing runtime environment was changed.

20 resource/coordinator tests passed in 17.29 s and the combined 77-test
bootstrap/coordinator/native-phase/media-budget suite passed in 36.68 s.
Logs `/tmp/ananta-meet-recovery-{coordinator,composition}.log`. These use real
SQL and synthetic authority ports; actual packaged runtime recovery is still
required before enabling this flag for a deployment.

The Worker signed HTTP client also negotiates the new response separately.
Its small `ReconnectReceiptGate` pins the first attempt's deadline/quarantine,
rejects phase/counter regression and accepts at most one grant per attempt.
Uncertain HTTP outcomes, invalid signatures or malformed receipts permanently
close that local gate instead of inheriting the read-only exchange retry rule.
The client stores only the original endpoint/room/deadline, not another copy
of the initial grant. 43 real loopback HTTP/signature/legacy/speaker-control
checks passed in 25.45 s (`/tmp/ananta-meet-reconnect-http.log`). No browser
rejoin or live recovery claim is made from those transport checks.

Upgrade Meet, every Hub and every assigned Worker before enabling recovery.
Fresh machine membership does not inherit the old peer's human receive consent:
only a new independently authorized consent/policy may enable those inputs.
