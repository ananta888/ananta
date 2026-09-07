# Bounded asynchronous dialog control reads

## Source-grounded problem

Before this change, the browser Worker called the signed Hub `exchange` synchronously in
its media tick. Even a valid 300 ms control round trip can starve the unchanged
200 ms speech queue. This is a known timing limitation independent of GPU
inference; the earlier selected-voice GPU failure is not yet attributed to it.
The Hub must remain the sole policy/task owner. Do not enlarge media queues,
weaken current-authority checks, retry writes or cache authority indefinitely.

## Incremental design

Introduce a Worker-local single-flight read adapter: one bounded HTTP future,
no independent loop, scheduling or delegation. The existing assigned runtime
alone polls it and applies verified state on the browser-owning thread. The
normal refresh cadence is one second and HTTP timeout stays six seconds.
Refresh cadence is anchored to request start, not completion, so transport
latency is not added twice to the next update interval.
An established projection expires after at most 2.5 seconds without a new
verified result; an expired/failed read terminates the bounded runtime and closes
its owned outputs. A result taking over 2.5 seconds is not applied as fresh.

After generation, a chat reply requires a control request **started after** its
completion was observed. An already in-flight older response may not satisfy
that fence. Mark it obsolete, discard it, then issue one fresh read. No parallel
backlog, stale-result replay or automatic retry of a denied read. Cancellation
drops pending application; the underlying fixed HTTP deadline bounds cleanup.

Keep this mechanism separate from chat admission, voice policy, browser media
ports and avatar selection (SRP/DIP). Preserve all negotiated/legacy wire fields.
Existing source revision, local browser membership, consent and PCM binding
checks still run on each relevant operation.

## Headless verification

Use deterministic futures/clocks for single-flight, deadline, obsolete-response,
post-generation and teardown tests. Then inject bounded control latency in a
private cross-repository synthetic voice scenario and require complete local PCM
plus non-silent correlated remote audio and current-policy revocation. Do not
substitute this synthetic acceptance for the pending real GPU run or public
TURN/production evidence. No serving instance or unrelated process is changed.

## Verification observations

The adapter is implemented without widening the 200 ms PCM queue or the 2.5 s
freshness boundary. All 78 focused control/speech/chat tests passed in 47.45 s.
A passive, bounded browser-RPC timing observer adds no browser calls and stores
only fixed operation tags/durations; its redaction/bounds test passed in 9.75 s.
Failure reports now snapshot runtime errors before teardown can append another
error. Consequently, the earlier GPU report's generic Hub-unavailable error is
not sufficient to attribute its original audio starvation to the Hub callback.

The initial 300 ms latency reproduction failed in 73.77 s. The first asynchronous
version also failed (42.46 s): completion-anchored cadence exhausted the freshness
margin. Request-start anchoring corrects that scheduling defect. A subsequent
run alongside a separate test suite still starved PCM (60.11 s), despite successful
308–385 ms callbacks and no runtime error before teardown; its residual cause is
not established. Do not claim arbitrary host-load resilience from this change.

The same latency case then passed serially in 58.17 s. A further serial regression
passed all four real private-browser cases in 175.09 s: legacy text, legacy speech,
image avatar and voice selection with 300 ms injected Hub latency, including
complete first speech, selected second voice and current-policy revocation.
These are technical/test-only observations, not a successful real-GPU rerun or
grounded production release evidence.

Further serial authority checks failed (two of three in 154.31 s; one of two
in 114.61 s). A timing-instrumented renewal case (109.22 s) reached actual lease
generation 2 and completed its first speech, but later expired control state and
lost the second output. Hub callback processing was 7–17 ms; observed browser
RPCs reached 245 ms and a media tick gap reached 402 ms. These observations do
not prove every individual stall's origin, but show the old two-second cadence
leaves only 500 ms for browser scheduling plus transport inside a 2.5 s lifetime.

The next correction schedules normal reads every one second from request start,
leaving 1.5 s scheduling/transport margin without extending authority lifetime.
This doubles nominal normal control reads; it does not add parallel requests,
retry a denial, grow the PCM queue or change Hub policy. Test this budget with
deterministic delayed polling, then re-run the failing real-browser cases.
There is no arbitrary host-load or real-time scheduling guarantee.

All 25 deterministic margin/observer/voice/interruption checks passed in 26.91 s.
The four-case browser follow-up could not exercise the changed cadence: all four
failed at `private-proxy-start` in 145.06 s. Even a network-isolated, no-mount
`node -e 'process.exit(0)'` readiness container timed out after 20 s; removing
owned empty proxy containers also timed out after 15 s. The daemon journal showed
concurrent deployment activity and connection/healthcheck errors. No daemon,
serving instance or unrelated container was restarted. Empty owned test-container
cleanup and the one-second-cadence browser acceptance remain pending daemon
recovery; these infrastructure failures are not passing/skipped runtime tests.
The final container-free regression passed all 129 control, observer, speech,
browser-port, publication, chat-pump and scenario-fixture tests in 54.45 s with
two Pytest workers. Targeted Ruff checks and TODO consistency validation passed.

Docker later recovered without a daemon restart. All owned leftover proxy and
readiness containers were removed; unrelated resources were preserved. Isolated
readiness checks passed for both pinned test images. The resumed one-second
cadence browser batch passed latency/voice, actual avatar lease renewal and
speech pause, but failed before parent-stop on the second correlated chat reply
(three passes/one failure, 236.29 s). The previous assertion did not expose
whether generation or browser source admission was missing. Passive bounded
acceptance/timing diagnostics now distinguish those phases without extra RPCs.
The isolated stop diagnostic then passed in 40.54 s. This is not a clean combined
batch or proof that every intermittent reply failure is resolved. Real GPU
delivery still lacks its rerun with sufficient available VRAM.
