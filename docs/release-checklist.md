# Release Verification Checklist

This checklist verifies that a v1.0.0 release candidate can be rebuilt from a clean checkout with the pinned dependency, tool and container inputs.

## Final GitHub Release Checklist

Before creating or publishing a release tag:

0. Follow the official phase path in `docs/release-golden-path.md`.
1. Confirm the release scope, version, and tag name follow `docs/release-process.md`.
2. Confirm all required branch-protection checks are green on the release commit.
3. Run the standard release gate and keep `release-verification-report.json`.
   Keep `ci-artifacts/client-surface-release-gate.json` from the same run.
4. Run the full local rebuild gate for release candidates that publish build artifacts or images.
5. Confirm `todo.json` status counters are synchronized.
6. Confirm release notes or changelog text is ready.
7. Confirm official release assets are intentional: verification report, checksums, selected docs or diagrams, and any container/image references.
8. Confirm no global repository secret is used where a protected release environment secret should be used.
9. Confirm security-sensitive changes have human review.
10. Create the tag only after the above evidence is available.
11. For voice-enabled releases: keep evidence for `docs/voice-quickstart.md` and `docs/voice-runtime-privacy.md` defaults.
12. For voice-enabled releases: verify `VOICE_STORE_AUDIO=false` by default and explicit approval enforcement for `/v1/voice/goal`.
13. For voice-enabled releases: keep optional smoke/live evidence (`RUN_VOICE_DOCKER_SMOKE`, `RUN_LIVE_VOXTRAL_TESTS`) when executed.

The GitHub release workflow publishes the official release asset bundle and `SHA256SUMS`. Build outputs not listed in `docs/release-process.md` are not official release artifacts.

## Standard Gate

Run the fast release gate first:

```bash
python scripts/release_gate.py --strict --compose-config --report release-verification-report.json
```

The standard gate checks:

- required release files exist
- `requirements.lock` and `requirements-dev.lock` contain pinned package entries
- Python runtime and dev source dependency lists match `pyproject.toml`
- frontend top-level dependency versions are exact and match `package-lock.json`
- GitHub Actions are pinned to commit SHAs
- CI uses Python `3.11.15`
- release Dockerfiles and Compose files use digest-pinned public image references
- global release tools are pinned
- backend and WSL/Ollama apt installs use fixed snapshots
- CI installs from lockfiles and runs the release gate
- client-surface release gate report is generated with per-surface status overview
- lite and distributed Compose configs render successfully
- `todo.json` schema validation uses auto-detected format (`category_meta` or `task_track`)

## Full Local Rebuild

Use the full local gate before tagging a release candidate:

```bash
python scripts/release_gate.py \
  --strict \
  --compose-config \
  --frontend-build \
  --build-images \
  --report release-verification-report.json
```

This additionally runs `npm ci`, `npm run build`, and backend/frontend/WSL-Ollama Docker image builds.

If Docker cannot pull images because of a local credential-store or registry issue, fix that environment issue before treating the release candidate as verified. Do not replace image tags with floating tags to work around registry failures.

On this WSL/Rancher Desktop setup, Docker pulls can fail when the Windows/Rancher `docker-credential-secretservice` helper is found on `PATH` without a running Freedesktop secret service. Use a clean Docker invocation for release verification:

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

## Two-Build Reproducibility Check

For a release candidate commit:

1. Create a clean checkout of the same commit in directory A.
2. Create a second clean checkout of the same commit in directory B.
3. Run the full local gate in both directories.
4. Keep both `release-verification-report.json` files as release evidence.
5. Compare the reports and the resolved version summary below.

Version evidence to record:

```bash
git rev-parse HEAD
python --version
node --version
npm --version
docker --version
docker compose version
python scripts/release_gate.py --strict --compose-config --report release-verification-report.json
python devtools/validate_todo_schema.py
python scripts/validate_todo_consistency.py --todo todo_last.json
```

The two builds are acceptable when the gate passes in both clean checkouts and both reports show all checks as `ok: true`.

## Release Blockers

Block the release if any of these are true:

- a release Dockerfile or Compose file uses `latest`, a moving major-only tag, or a public image without a digest
- release CI uses a floating `actions/*@vN` tag
- release CI uses open Python `3.11` instead of Python `3.11.15`
- CI installs backend dependencies from `requirements.txt` or `requirements-dev.txt`
- frontend release builds use `npm install` instead of `npm ci`
- `opencode-ai` or `@mermaid-js/mermaid-cli` is globally installed without an explicit version
- backend or WSL/Ollama release Dockerfiles install apt packages without a fixed snapshot
- `todo.json` release-pinning status counters are out of sync
- backend or frontend image builds fail in a clean environment
- voice-enabled release misses privacy defaults (`VOICE_STORE_AUDIO=false`) or explicit goal approval boundary

## Fixed Release Inputs

Exact image tags plus digests are required for public release images. Debian and Ubuntu apt installs use snapshot `20260406T000000Z`. Local build-output image names such as `ananta-...:local` remain local-only placeholders and are not registry release inputs.
