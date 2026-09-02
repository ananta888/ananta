# Hub Evidence-bound Pytest Gates

The Hub Evidence pytest runner turns an actual clean-revision test execution
into a pre-reserved, immutable local evidence run. It does not promote a log or
an already completed command after the fact.

## Lifecycle

1. The runner rejects a dirty tracked worktree and resolves the exact Git SHA.
2. It hashes the reviewed profile, every declared source file and the trusted
   runner/registry implementation.
3. The Hub admits that repository bundle as `SRC_*` and reserves a `RUN_*`
   identity before pytest starts.
4. Pytest receives only `ANANTA_HUB_EVIDENCE_ASSIGNMENT`, a closed,
   digest-protected assignment projection.
5. The Hub records a redacted result summary under the exact assignment and
   dispatch lease. A failed, timed-out, empty or unexpectedly skipped suite is
   terminalized as failed.
6. Release verification checks the persisted run, sources, task, revision and
   evidence scope. No step requires a person.

The profiles permit only direct `python -m pytest` argument vectors. Shell
execution, pytest configuration replacement, path traversal and secret-like
profile environment keys are rejected. Raw stdout and stderr are shown to the
operator but only their SHA-256 digests enter the evidence report.

## Profiles

```bash
python scripts/run_hub_evidence_pytest_gate.py \
  --profile config/release-gates/hub-evidence/agent-safety.v1.json \
  --output artifacts/agent-safety-hub-evidence.json \
  --junit /tmp/ananta-agent-safety.xml

python scripts/run_hub_evidence_pytest_gate.py \
  --profile config/release-gates/hub-evidence/jmap-local.v1.json \
  --output artifacts/jmap-hub-evidence.json \
  --junit /tmp/ananta-jmap.xml

python scripts/run_hub_evidence_pytest_gate.py \
  --profile config/release-gates/hub-evidence/local-model-runtime.v1.json \
  --output artifacts/local-model-runtime-hub-evidence.json \
  --junit /tmp/ananta-local-model-runtime.xml
```

The Qdrant profile requires the digest-pinned TLS service and its disposable
credentials. The `qdrant-integration` CI job provisions that environment and
invokes the same runner with
`config/release-gates/hub-evidence/qdrant-integration.v1.json`.

Generated root-level `artifacts/*.json` reports and the SQLite registry under
`data/` are runtime data and are not committed. A deliberately selected stable
report may be copied into `artifacts/domain/` only when it contains no secrets,
volatile timestamps or environment-specific paths.

## Scope

These four profiles issue `local` evidence. They prove the repository-bound
automatic gate on the observed machine or CI runner. They do not claim that an
absent external JMAP account, LM Studio/Ollama model, reference hardware or
production Qdrant deployment was exercised. Such evidence needs a separately
declared `external` or `production` profile and the corresponding real runtime.
