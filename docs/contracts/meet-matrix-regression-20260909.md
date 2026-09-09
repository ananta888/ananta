# Meet matrix regression, 9 September 2026

The grouped `tests/test_meet_*.py` run at Ananta `8b305d95a` collected
4,877 cases and finished in 1,895.75 seconds (31 minutes 35 seconds):
4,787 passed, 82 explicitly skipped, eight failed and one teardown error.
It is a technical test observation, not a pre-reserved production release run.
Opt-in native/GPU/public checks were not enabled; their skips are not passes.

Two fixture defects account for the nine reported failures/errors:

- Eight bootstrap parameter combinations supplied an unrestricted `Mock()` as
  a Worker. Its `publisher_url` became another Mock, correctly rejected by the
  strict publisher router. The fixture now supplies an explicit private Worker
  URL and checks that the router retains that exact injected Worker. Production
  URL validation is unchanged (LSP: a double must meet its real port contract).
- The enabled Meet composition test replaced the process-global database engine
  until pytest's monkeypatch teardown. The autouse isolation guard ran first and
  correctly rejected the changed database. A locally scoped patch now restores
  the original engine before returning, and the test disposes only its own
  temporary engine. The guard is not weakened. The existing production global
  engine dependency remains DIP debt; this patch removes the test's hidden
  cross-fixture side effect without redesigning application persistence.

The complete two affected test modules plus the isolation-guard tests passed:
75 cases in 37.93 seconds, using two pytest workers. This is a targeted repair
verification, not a claim that the full 4,877-case matrix has been rerun green.
Native startup/source-timing failures and the long/public gates remain separate
open work in MAP-29 through MAP-32. The user's unrelated Compose/Caddy and
frontend runtime files were not changed.

A subsequent broader targeted batch at `1b2206db2` passed391 cases in141.07
seconds with15 explicit skips. It included publisher/bootstrap composition,
durable capacity and role binding, phases, evidence-runner inputs, startup
observers and LiveKit observation compatibility. Thirteen skips were the
integration-directory default opt-out; those exact self-contained LiveKit
adapter/runtime-probe tests were then explicitly enabled and all13 passed in
14.98 seconds. The remaining two skips require the private native phase gate.
These LiveKit checks test deterministic adapters and closed probe behavior;
they are not a claim of a new live LiveKit server or public media run.

## Updated isolated matrix

At Ananta `4c53b51f3`, a clean private worktree ran 302 Meet/dialog-browser and
LiveKit test files with four pytest workers, an explicit 120-second default
case deadline and a 3,600-second outer process-group deadline. The 13
self-contained LiveKit integrations were explicitly enabled. This matrix is a
technical observation, not a Hub-reserved production release reference.

It completed in 1,035.90 pytest seconds (17 min 15.90 s), 1,038.762 controller
seconds: 5,021 cases, 4,937 passed, 82 explicit skips and two failures. Skipped
native/GPU/public gates are not passes. The prior two bootstrap/isolation
fixture defects did not recur. The two new failures were environment-dependent
test assumptions, not evidence of an actual GPU model or revocation failure:

- The GPU resource-projection unit mocked capacity and Docker but still
  required `data/meet-media/models` in the checkout. The clean worktree correctly
  had no runtime model download. Its test-only helper now accepts an explicit
  model directory for isolated unit testing; real callers retain the original
  repository default and actual capacity check. Missing/file/symlink directories
  still fail before Docker inspection. No model or GPU acceptance is simulated
  as hardware evidence.
- The read-lock test used the application-global database while assuming
  shared-cache SQLite semantics. This matrix explicitly used WAL, which allows
  the writer to succeed on its first attempt while the reader remains open.
  The test now owns separate explicit shared-cache and WAL databases, two real
  pooled connections and a minimal SQL lock surface. It verifies one bounded
  retry only for shared-cache, none for WAL, and the actual committed revocation.
  The four separate real organization-graph cases still exercise full schema
  and exact targets; no foreign-key or runtime contention policy was relaxed.

This removes hidden filesystem/global-database coupling (DIP) and keeps fixture
topology separate from revocation behavior (SRP). Neither repair changes Worker
or Hub production runtime. All 89 affected/resource/cleanup/container checks
passed in WAL mode in 41.06 seconds; Ruff passed. Both repaired modules also
passed all 20 cases under the default-memory application mode in 17.58 seconds.
Final combined confirmation follows separately; the failed aggregate is retained.

## Confirmed matrix and MAP-29 boundary

The identical combined matrix at clean Ananta `1ece01072` passed with four
workers, explicit WAL, enabled self-contained LiveKit integrations and unchanged
case/outer deadlines: **4,943 passed, 82 explicitly skipped, zero failures or
errors**, 5,025 total cases, 1,033.58 pytest seconds (17 min 13.58 s),
1,036.439 controller seconds. The four added cases cover the model path negatives
and both explicit SQLite modes. This is the final confirmation of the two
fixture repairs; neither preceding failed aggregate is relabeled as passed.

Together with Meet `c4ef486`'s passing current Human/Pair/Chromium/Firefox and
security matrix, the actual paired-idle screen/chat/stop reference and the
two-installed-Worker media/reconnect/resource reference, MAP-29's headless
contract, security, lifecycle, transport-observation and compatibility criteria
are fulfilled. All six criteria were checked; this closes the test-matrix
implementation task, not the whole track.

The older unattributed 349-RTP-packets/zero-decoder startup incident is retained
as a historical, not causally resolved observation under MAP-30's runtime
follow-up. Current successful cases are not claimed to explain its cause.
GPU hardware recovery, the normal two-hour reference, public operator trust,
OIDC/TURN/independent-receiver acceptance and staged rollout remain in MAP-30,
MAP-31 and MAP-32. No native skip, test identity or health response is promoted
to production evidence, and the partial track is not archived.
