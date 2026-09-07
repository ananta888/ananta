# Silent participant in a Hub-allocated room (MAP-07/29)

## Source check and private acceptance plan

The new allocation API prepares only a persisted room binding. Existing dialog
gates start in a room already created by their human fixture and immediately
enable chat/screen/media paths; they do not prove a silent first machine in a
freshly Hub-allocated room. Add a separate short gate, not another branch in the
large media scenario.

Reuse Meet's existing private TLS/STUN/browser fixture without changing its
production code or running deployment. The fixture's original human room is
test-owned and distinct. After readiness, the real Hub allocation service uses
its normal random room factory and isolated SQL CAS. A bounded private driver
binds its observation to that exact new room and first verifies it is empty.

Start a normal Hub dialog task with only `chat.send`, chat/audio off and no
publish capability. Use the actual Worker runtime and a separate sandboxed
Chromium container. Verify real Meet membership, the fixed KI name, exactly one
machine and zero publications before any human joins that room. Move the
synthetic human fixture into the allocated room through its normal authenticated
UI; verify two participants, no machine media and no human capture calls.

Finally stop the Hub task and require the actual machine participant to leave
within the existing bounded teardown budget. No human approval, source grant,
model/GPU call, production identity, external TURN or public endpoint is needed.
Record this as single-host private integration with synthetic policy, not
production room provisioning or a multiple-agent acceptance result.

Keep infrastructure/stdio observation, scoped Hub fixture composition and the
short scenario separate (SRP/ISP/DIP). Private driver commands may bind/observe
only one canonical test room and move only its own synthetic human. They cannot
submit tasks, signing grants, arbitrary JavaScript, credentials or policy. Bound
input bytes, command times and exact owned-resource cleanup; check the current
Meet browser build before provisioning.

## Verification

The fixture unit/SQL allocation suite passed 62 tests in 43.97 seconds, with
the opt-in browser case explicitly skipped in that unit invocation. The first
live attempt then failed its stale-build preflight in 8.97 seconds, without
provisioning containers. Meet had advanced concurrently to `4d40b45`; a fresh
private build completed in 7.438 seconds and was used unchanged for both gates.

The real silent-first-machine gate passed in 25.24 seconds, observing four
successful Hub exchanges, zero publications/capture and 1,193.69-ms removal.
The final gate additionally checks the actual human browser's visible
`Ananta (KI)` heading. It passed in **20.79 seconds**, with four validated Hub
exchanges, zero machine publications, zero human capture calls, a never-called
media executor and **1,146.63-ms** removal following Hub cancellation. The real
task remains `cancelled`; a late Worker finish cannot override it.

The fixture also checks partial setup cleanup, strict private commands, bounded
partial-line reads, no side effects merely from importing its Node driver and
bounded cleanup escalation for exactly its own process. A nonzero bridge
cleanup exit is a gate failure, not silently successful cleanup. These are
single-host technical observations, not production SRC/RUN evidence.

The final combined room/recovery regression passed 193 tests in 74.44 seconds.
That deterministic suite is separate from the two actual browser invocations.
