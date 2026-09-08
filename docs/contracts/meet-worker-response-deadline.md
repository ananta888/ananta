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

## Reproduction and implementation boundary

The initial real signed-body regression failed in 8.80 seconds: the old client
accepted the complete synthetic result even though continuous byte delivery
had outlived its 250-ms remaining budget. The fixed client converts that
original budget once, refuses already expired calls before address resolution,
rechecks after request preparation, and uses the shared bounded reader. Size
overflow retains its original 502 code and every response context closes.

The existing Worker always emits ordinary Content-Length responses. Transfer
encodings are now explicitly rejected before reading: stdlib chunked `read1`
may parse an entire chunk-header line internally, which is not one underlying
read and would reopen the trickle problem. No supported Worker response changes.
Initial response-header parsing and any single inactive socket read remain
outside the new absolute body-loop checks; whole-request deadline hardening
is a separate limitation, not claimed solved by this slice.

The regular-body guard is a separate small Hub IO adapter shared by media
success, dialog-start and signed-capability-failure reads, so transfer-encoding
rejection is consistent without copying a body reader. Each caller still owns
its own maximum, deadline, signature domain and error projection. It does not
alter shared Persona HTTP consumers or embed governance in the Worker.

The first expanded regression recorded 95 passes and three 10-second timeouts
during global application-fixture import, in 101.91 seconds overall, while the
large dependency image was building. The affected HTTP cases did not execute.
Repeat them after the build without increasing deadlines; these setup errors
are not passing transport tests or evidence that a product fix is necessary.

The unloaded-build repeat passed all 101 tests in 45.25 seconds, with the
original deadlines and both additional transfer-encoding rejection paths.
The subsequent actual GPU/browser repeat failed in 118.63 seconds waiting
for its first correlated answer: zero generated answers, zero inference
failures and zero speech acceptances were observed. No inference invocation
was observed at that checkpoint. This is not a passing GPU regression or
proof of a transport failure; investigate the pre-inference chat path without
changing answer budgets or adding retries. The earlier successful GPU runs
remain historical observations, not substitutes for this failed repeat.

The independent real GPU HTTP component gate then passed in 55.23 seconds:
Qwen generated 13 output tokens, Piper-CUDA returned 69,632 non-silent PCM
samples and NVENC returned 64,468 video bytes. The component observation
explicitly reports no Hub-dialog or remote-delivery verification. It verifies
actual successful media transport/inference, not the failed browser chat path.

The next diagnostic step observes existing chat poll/ACK, speech preparation
and spoken-callback calls only. A separate test-only observer keeps counters
and at most eight allowlisted rejection codes; no event bodies, IDs, keys,
extra browser operations or retries. Exact return values and exceptions must
remain unchanged. This keeps path diagnosis separate from the already broad
speech fixture (SRP), and does not change production admission policy.
