# Release Verification Checklist

This checklist verifies that a v1.0.0 release candidate can be rebuilt from a clean checkout with the pinned dependency, tool and container inputs.

## Standard Gate

Run the fast release gate first:

```bash
python scripts/release_gate.py --compose-config --report release-verification-report.json
```

The standard gate checks:

- required release files exist
- `requirements.lock` and `requirements-dev.lock` contain pinned package entries
- Python runtime and dev source dependency lists match `pyproject.toml`
- frontend top-level dependency versions are exact and match `package-lock.json`
- release Dockerfiles and Compose files avoid floating image tags
- global release tools are pinned
- CI installs from lockfiles and runs the release gate
- lite and distributed Compose configs render successfully

## Full Local Rebuild

Use the full local gate before tagging a release candidate:

```bash
python scripts/release_gate.py \
  --compose-config \
  --frontend-build \
  --build-images \
  --report release-verification-report.json
```

This additionally runs `npm ci`, `npm run build`, and backend/frontend Docker image builds.

If Docker cannot pull images because of a local credential-store or registry issue, fix that environment issue before treating the release candidate as verified. Do not replace image tags with floating tags to work around registry failures.

On this WSL/Rancher Desktop setup, Docker pulls can fail when the Windows/Rancher `docker-credential-secretservice` helper is found on `PATH` without a running Freedesktop secret service. Use a clean Docker invocation for release verification:

```bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
DOCKER_CONFIG=/tmp/ananta-docker-config \
python scripts/release_gate.py \
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
python scripts/release_gate.py --compose-config --report release-verification-report.json
```

The two builds are acceptable when the gate passes in both clean checkouts and both reports show all checks as `ok: true`.

## Release Blockers

Block the release if any of these are true:

- a release Dockerfile or Compose file uses `latest`, a moving major-only tag, or a public image without a digest
- CI installs backend dependencies from `requirements.txt` or `requirements-dev.txt`
- frontend release builds use `npm install` instead of `npm ci`
- `opencode-ai` or `@mermaid-js/mermaid-cli` is globally installed without an explicit version
- `todo.json` release-pinning status counters are out of sync
- backend or frontend image builds fail in a clean environment

## Residual Drift

Exact image tags plus digests are required for public release images. Debian snapshot-backed apt installs remain the next hardening step and are documented in `docs/release-dependency-locking.md`.
