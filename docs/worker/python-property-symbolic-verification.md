# Python property and symbolic verification

Ananta can delegate bounded Python verification to an optional, isolated
Worker. Hypothesis is the stable PR-facing property backend. CrossHair is an
experimental targeted backend for contracts, generated coverage candidates,
behavior comparisons, and selected Hypothesis properties.

No-counterexample results are bounded observations, not proofs of correctness.
Normal example tests, type checking, security checks, integration tests, E2E
tests, and review remain authoritative for their respective concerns.

## Boundary and control flow

```text
Hub Evidence Registry reserves SRC_/RUN_ identities
  -> Hub selects allowlisted targets and immutable profile
  -> Hub emits one closed VerificationAssignmentV1
  -> isolated Worker runs one adapter with no network or secrets
  -> Worker emits one closed VerificationReportV1
  -> Hub validates lease, assignment, revision and digests
  -> concrete reproduction may become a reviewed regression-test proposal
```

The Worker cannot create Tasks, expand target scope, enable plugins, change the
profile, issue evidence identities, or make promotion decisions. CodeCompass
may suggest changed symbols. The deterministic selector intersects those
symbols with `config/verification/property-catalog.v1.json`; on CodeCompass
failure it uses only explicit allowlisted targets and never scans the complete
repository implicitly.

The assignment contract applies a backend-specific grammar: Hypothesis paths
must be explicit `tests/verification/*.py::test_*` node IDs, while CrossHair
receives dotted Python symbols. The Worker independently intersects them with
the catalog immediately before execution. Leading options, absolute paths,
path traversal, control characters, option separators, unknown symbols and
`--option=value` plugin/unblock variants fail closed.

## Install and run

Install the optional stack:

```bash
uv sync --extra dev --extra verification
```

Run the stable property and state-machine pilot:

```bash
uv run pytest -q -p no:cacheprovider tests/verification -m "not verification_real"
uv run python scripts/run_python_verification_fast_gate.py --repeat 20
```

Run the bounded real-tool pilot:

```bash
uv run pytest -q -p no:cacheprovider tests/verification/test_real_toolchain.py
```

The container profile is `docker/compose-next/compose.verification-worker.yml`.
It uses `network_mode: none`, a read-only root filesystem, all Linux
capabilities dropped, `no-new-privileges`, no Docker socket, no secret mount, a
read-only repository, and one task-scoped writable workspace. `--unblock
EVERYTHING` and plugin options are rejected by the process boundary.

## Profiles and status semantics

- `hypothesis-pr-fast`: deterministic, release-gating property tests.
- `crosshair-targeted`: bounded contract exploration; no result is called a
  proof.
- `crosshair-cover-targeted`: produces candidates only; generated code is not
  promoted automatically.
- `crosshair-backend-nightly`: compares five selected properties using the
  solver-backed Hypothesis backend.
- `crosshair-diff-experimental`: compares two explicitly assigned symbols.

`passed` is reserved for completed finite property runs.
`passed_with_bounded_search` records successful bounded tool execution without
claiming completeness. `inconclusive`, `unsupported`, `timed_out`,
`failed_to_reproduce`, `policy_denied`, and `tool_error` remain distinct and
can block a release only through explicit Hub policy.

Pytest-backed adapters load an internal result plugin. Reports contain observed
collection, pass and failure counts plus bounded-search metadata. A violated
property becomes `counterexample_found`; collection/import failures,
unparseable output, process failures and timeouts retain separate reason codes.
The actual number of Hypothesis examples is not available through the Pytest
reporter, so `cases_executed` stays zero and `case_count_observed=false` instead
of copying the configured budget into an observation field.

## Counterexamples and promotion

Only JSON-concrete values cross the Worker boundary. Symbolic proxies and
custom tool objects are rejected. CrossHair call output is parsed as balanced
Python literals through `ast.literal_eval`; calls, starred arguments, malformed
nesting and ambiguous target mappings are unsupported and cannot be promoted.
All safe findings are retained rather than only the first. A counterexample
includes a target-derived standalone command and a digest of the exact
candidate. Changing arguments, invariant, target, or command invalidates
promotion. Tests and fake adapters
use `evidence_scope=test`; mutating that scope invalidates the Hub projection
digest and cannot satisfy a production gate.

Promotion remains a Hub decision. An auto-approval policy may make a headless
run fully automatic. A denied policy returns a bounded machine-readable result;
no test or productive bounded run waits for human interaction.

## Property design and diagnosis

Prefer small typed functions with an existing schema constraint, contract,
test, or documented invariant. Extract pure decision cores behind ports when a
service depends on time, randomness, databases, files, processes, or network.
Do not treat an LLM-proposed property as the specification.

Common reasons:

- `verification_no_bounded_targets`: provide an explicit cataloged symbol.
- `verification_dispatch_lease_stale`: reserve a fresh Hub run and dispatch.
- `budget_timeout`: reduce the target set or use a nightly profile.
- `output_budget_exceeded`: inspect the bounded raw log; do not silently raise
  the limit in Worker input.
- `crosshair_*_failed`: verify Python/tool versions and target purity.
- `failed_to_reproduce`: do not promote or gate on the symbolic observation.

## Disable and uninstall

Disable the corresponding Hub profile or omit the verification Compose overlay.
Remove the `verification` extra from the development environment if desired.
Core imports and ordinary Pytest paths do not depend on Hypothesis, CrossHair,
hypothesis-crosshair, or Z3, so disabling the feature has no runtime effect.

The pinned package and license inventory is in
`config/licenses/python-verification.v1.json`. The current CrossHair upstream
is Alpha and must remain isolated even when its checks are useful.

Optional generator candidates and their Go/Hold/No-Go decisions are recorded
in `docs/research/python-verification-generator-candidates.md`. They remain
outside the Worker Core and outside production evidence.
