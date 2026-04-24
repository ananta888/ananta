# Release Golden Path

This document defines the official release and operations approval path for Ananta.

Goal: release candidates, final releases and operator communication follow one predictable route instead of improvised checks.

## Roles And Ownership

- Release owner: coordinates the candidate, evidence and final decision.
- Governance reviewer: checks security, policy and audit-relevant changes.
- Operations reviewer: checks deployment notes, rollback path and runtime compatibility.
- Hub remains the control plane for Ananta workflows; release gates verify the product and deployment artifacts around it.

## Phase 1: Release Candidate

1. Select a candidate commit on the protected release branch.
2. Confirm scope, version and candidate tag, for example `v1.0.0-rc.1`.
3. Run the standard quality path:
   - `make check`
   - `python scripts/release_gate.py --strict --compose-config --report release-verification-report.json`
   - Keep `ci-artifacts/client-surface-release-gate.json` from the release gate as merge-review evidence.
4. Run the full local rebuild gate for RCs that publish images or build artifacts:
   - `python scripts/release_gate.py --strict --compose-config --frontend-build --build-images --report release-verification-report.json`
5. Trigger `.github/workflows/nightly-rc-validation.yml` with `validation_depth=release-candidate`.
6. Store release evidence:
    - release verification report
    - client surface release gate report (`ci-artifacts/client-surface-release-gate.json`)
    - release candidate verification report when image gate is used
   - SBOM
   - checksums
   - relevant CI run links
7. Record workflow-run and artifact references in `docs/release-evidence-register.md`.

Exit criterion: the candidate is only valid when gates are green and release evidence is attached to the candidate decision.

## Phase 2: Final Approval

1. Review open release blockers from `docs/release-checklist.md`.
2. Confirm no release-relevant TODO counters or changelog notes are stale.
3. Confirm governance mode, product profile and runtime compatibility notes are documented.
4. Confirm rollback and operator communication are ready.
5. Create or approve the final tag only after the evidence above exists.
6. Publish through `.github/workflows/release.yml` or a protected release tag push.

Exit criterion: the final release has a tag, GitHub Release, release asset bundle, checksums and human-readable release notes.

## Phase 3: Operations Handoff

1. Share the release tag, asset bundle and checksum location.
2. Share supported runtime profile and governance-mode expectations.
3. Share upgrade, smoke-test and rollback notes.
4. Watch first deployment signals:
   - hub health
   - worker registration
   - goal creation
   - task execution
   - governance or policy blocks
5. Record follow-up issues for regressions, missing docs or operator friction.

Exit criterion: operators can identify what was released, how it was verified, how to deploy it and how to roll back.

## Official Commands

Standard gate:

```bash
python scripts/release_gate.py --strict --compose-config --report release-verification-report.json
```

The command also runs `scripts/audit_client_surface_entrypoints.py` and writes the surface status report to:

`ci-artifacts/client-surface-release-gate.json`

Full local rebuild gate:

```bash
python scripts/release_gate.py \
  --strict \
  --compose-config \
  --frontend-build \
  --build-images \
  --report release-verification-report.json
```

WSL/Rancher Desktop release verification variant:

```bash
ANANTA_DOCKER_CLEAN_PATH=1 \
ANANTA_NPM_COMMAND="npx -p node@20.19.5 node /usr/bin/npm" \
python scripts/release_gate.py \
  --strict \
  --compose-config \
  --frontend-build \
  --build-images \
  --report release-verification-report.json
```

## Release Decision Record

Use this minimum record for every RC and final release:

```markdown
Release:
Commit:
Candidate or final tag:
Release owner:
Governance reviewer:
Operations reviewer:
Quality gate:
Release gate:
RC workflow:
SBOM/checksums:
Known risks:
Rollback path:
Decision:
```

## Blockers

Block release approval when any of these are true:

- required gates are red or missing
- release evidence cannot be reproduced from the candidate commit
- Docker, Python, Node or GitHub Actions versions are unpinned
- security-sensitive changes lack review
- rollback path is missing for operator-impacting changes
- release notes omit API, security, runtime or deployment behavior changes
