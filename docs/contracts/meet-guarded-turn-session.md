# Native Hub/Worker/Meet session behind the fixed-endpoint guard

## Source audit (2026-09-09)

Ananta `979c3be9a` verifies a real packaged Worker listener, authentication,
health and process-level HTTP/UDP/DNS ceiling. It does not yet prove a media
session behind that ceiling. Companion `b29fa60` adds a private authenticated
TURN fixture and UDP/TCP browser observations, but its two-Worker stdio bridge
does not select that transport and Ananta's packaged Worker fixture does not
share a guard namespace.

Reuse those existing components. Extend only the private bridge with a closed,
default-direct ICE option and bounded receiver-side selected-pair counters.
Each packaged Worker receives its **own** guard on the fixture's verified
internal bridge. Its immutable egress policy admits exactly the native Hub
callback address/port, the private Meet TLS origin on 443 and the one selected
TURN listener transport on 3478. Incoming 8094 is restricted to the Hub caller.
No UDP range, direct peer endpoint, host network, host firewall, public trust,
production key, host capture or Worker orchestration is introduced.

The existing synthetic headless Hub policy, native Task assignment, signed
callbacks, browser source ownership and independent cancellation/revocation
remain unchanged. First reuse the companion's test-only relay-from-start
adapter, including in the isolated Worker context. It may use only the exact
successful machine-session response's server-issued REST credentials. It must
not mint a grant, add a session request, relax the guard or change a Task.
This establishes forced TURN, not the normal delayed ICE fallback. The latter
must be checked separately with the real unchanged Worker behavior; preserve
any failure instead of describing the test adapter as a production fix.

Verify moving screens with two separately assigned packaged Workers, selected
successful relay pairs with growing byte counters, continued survivor media
after one cancellation and bounded final revocation. The existing guard
counter gates remain the negative process-level proof. Run focused helper,
boundary and legacy tests, then actual private UDP/TCP sessions. Broader audio,
GPU, public NAT/TLS, long soak and production evidence remain separate gates.

Keep namespace infrastructure, closed bridge options and passive media
observations separate (SRP/DIP); preserve existing fixture defaults. The broad
two-Worker scenario is existing test-composition SRP debt: add small helpers,
not more inline policy/transport logic.

## Initial multi-party TURN investigation

The direct two-Worker baseline passed in 45.96 seconds. Two guarded UDP cases
failed in 67.79/68.76 seconds: one receiver connection carried 37/38 decoded
frames, while the other had complete gathering and an offer/answer but no
active ICE/DTLS/SCTP transport. There were no transform or Hub exchange errors.
Add only bounded numeric ICE error diagnostics before changing behavior.

A concrete infrastructure hypothesis is Coturn allocation bandwidth: the
existing **two-peer** fixture uses a 4,000,000 bytes/s aggregate budget and
1,000,000 bytes/s per allocation. Coturn reserves the per-allocation limit
even when the client supplies no bandwidth attribute; a three-participant
full mesh needs six endpoint allocations, not two. The version-pinned
[allocation path](https://github.com/coturn/coturn/blob/4.17.0/src/server/ns_turn_server.c#L1409)
and [capacity accounting](https://github.com/coturn/coturn/blob/4.17.0/src/apps/relay/netengine.c#L168)
show rejection once the aggregate allocation budget is exhausted.

If confirmed by the actual ICE failure codes, add a closed three-participant
**test** profile with 500,000 bytes/s per allocation and the **unchanged**
4,000,000 bytes/s total budget (capacity for eight allocations, six needed).
Retain the original two-peer defaults, all quotas, endpoints, relay-port range,
CPU/RAM/PIDs and media deadlines. No unlimited capacity or automatic resource
expansion. Then repeat the real session and original fixture checks; the
hypothesis alone is not a fix or production throughput proof.

The third unchanged-budget case failed in 68.62 seconds and the bounded
Worker-side observers recorded actual ICE error code 486 on both Workers.
The isolated direct baseline remained green. Apply the closed three-party
budget profile next, then verify whether it resolves these allocation failures;
do not raise the aggregate ceiling or alter a grant/guard to obtain a pass.
