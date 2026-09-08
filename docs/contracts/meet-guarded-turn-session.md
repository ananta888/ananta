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
