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
