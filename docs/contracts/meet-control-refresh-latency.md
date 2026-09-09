# Diagnose bounded control freshness during GPU speech

Source audit: Ananta `89956ecaf`, Meet `1c7cd3c`; packaged Worker
`sha256:01db5060da48dd832588c84e00e2454b6792020a8236657ed1936f7df33de984`.
The client probe itself passes 330 root regressions, the complete isolated Meet
check and both real two-packaged-Worker screen/persona/speech cases (105.70 s).

The subsequent actual selected-voice GPU dialog failed in 110.73 s. Both Qwen/
Piper answers were generated: 46,592 neutral-voice samples, then 109,824 whisper
samples. During the second output the runtime raised
`meet_dialog_control_state_stale`; the pending output was cleared. Failure
diagnostics show a 25.06-ms polling gap, 18.08 ms beyond the established freshness
deadline, and a still-pending 1,609.21-ms control request. A prior browser call
classified only as `other` took 1,320.71 ms. Completed Hub-domain exchanges were
48–64 ms. These measurements do not establish whether delay occurred before
the domain handler, in HTTP transport, executor scheduling or browser execution.

Do not extend the 2.5-second freshness/stop boundary or relabel the interrupted
audio as delivered. A successful repeat alone is not a timing fix. First extend
only private test observation: distinguish bounded browser operation categories,
correlate numeric monotonic timings, measure the existing complete Worker-Hub
exchange independently of its domain handler, and retain a bounded control-state
transition history. No additional browser/Hub request, result consumption,
retry, authority write, keys, payloads, URLs or exception text in diagnostics.

Verify observers with virtual clocks and the actual wrapped methods, including
native return/exception identity and bounded copied output; then repeat the
same actual GPU scenario to localize the delay. Any scheduling change must have
a deterministic failing regression and preserve fresh Hub authorization,
single-flight/backpressure, original assignment expiry and terminal denial.
MAP-08/11/24 remain open. This is synthetic-policy technical runtime testing,
not production release evidence or a completed cold-start/soak/public gate.

SRP/DIP: transport, browser and control-state observation remain separate small
test adapters. Existing broad runtime/test composition debt is preserved; no
Worker-owned task scheduler or timing policy belongs in an observer.

Implemented passive observers now share numeric monotonic timestamps. Browser
tags recognize mixed-case internal speech/screen names; complete Hub transport
keeps 12 recent calls and at most four in-flight timing rows with explicit
overflow. The controller retains only 16 transitions, never each PCM tick,
and reports pending/running/done plus the due-time delta without consuming a
future. Domain failures are reduced to fixed known codes; returned rows are
copies. Native calls, return values and exception identity remain unchanged.
The first 34 observer/control/speech/scenario checks passed in 29.03 s. Repeat
the real GPU scenario next; these tests establish diagnostic behavior, not a
runtime latency fix.

Two identical actual-GPU repeats with the passive observers passed in 114.63
and 113.69 seconds. The first generated 44,800 and 182,528 non-silent samples;
local/remote revocation took 812.53/817.01 ms. Its retained completed transport
calls were 76–101 ms, with no in-flight call at teardown. The expected final
403 was the real Hub's `meet_dialog_task_inactive` after cancellation, not an
unexpected transport failure. The only retained slow browser call took 70 ms.
These are useful baselines, not a reproduction or fix of the earlier 1.3-second
browser/1.6-second pending-request incident. Keep that intermittent failure open;
do not spend unbounded GPU repetitions looking for a green result or change the
freshness policy without a deterministic causal regression.

## Reconnect multimedia: file-SQLite connection churn

On 2026-09-09 the new packaged two-Worker reconnect/media scenario reproduced
a different, Hub-domain delay. Four diagnostic runs failed in 91.10, 78.78,
89.19 and 91.02 seconds; the earliest uninstrumented media attempt failed in
62.89 seconds before speech observation. Do not erase these failures or treat
the initial speech failure as independently explained.

The bounded native-port observer measured control exchanges at 2,428/2,665 ms,
authority checks around 200–300 ms, and individual Task reads around 40 ms.
Recent browser calls were approximately 45–76 ms. The owned SQLite test database
contained 3,055 schema objects. Forty read-only connection/read iterations on
that retained test database averaged 6.125 ms with connection reopening versus
0.146 ms with reuse (including initial opening). This is a local technical
comparison, not a production benchmark or the earlier GPU/browser incident.

`ANANTA_SQLITE_POOL_SIZE=1..64` now optionally reuses that many physical
file-SQLite connections. Zero retains the existing `NullPool` default;
PostgreSQL and both in-memory SQLite profiles are unchanged. Overflow is zero
and saturated checkout fails after 250 ms. Hub restart is required. This is
not a Session, transaction, result or policy cache: each authority check still
performs its own fresh queries, and rollback-on-return remains enabled.
Capacity must be selected for the deployment; bounded exhaustion remains a
failure, never automatic authority. No serving deployment was changed.

Tests prove exact physical connection counts across independent Sessions,
external revocation visibility, rollback of uncommitted state, foreign keys,
recovery after saturation and actual configured Hub engine selection. The
pooled WAL/isolation/recovery/observer group passed 48 tests in 27.31 seconds.
The corresponding native multimedia run, with eight connections, reached both
rejoins and the second spoken reply with recorded control calls around 38–65 ms.
It then failed in 67.18 seconds at the third interruption: the test had navigated
to Chat and incorrectly waited for a participant counter rendered only in Live.
That independent fixture navigation issue requires its own correction and a
complete repeat; this intermediate result is not a passed reconnect gate.

After the explicit test-only Live navigation correction, the entire packaged
multimedia reconnect gate passed in 57.78 seconds. Two actual recoveries took
6,524.61/6,010.07 ms, followed by exhausted-attempt stop in 437.82 ms. Old audio
was absent across all observed receiver tracks, new memberships required fresh
receive consent, both moving screens/personas returned, and the other Worker
continued independently. Retained late control exchanges were 30.56–55.67 ms.
This closes the measured connection-churn failure for the explicitly pooled
single-host profile, not every latency cause or the earlier GPU incident.

SRP: pool-option policy lives in a small infrastructure module, not the Meet
authority or Worker. Existing large Settings/database composition remains
preserved debt; no domain API or Hub/Worker responsibility is changed. All
observations here use synthetic policy, not production release evidence.
