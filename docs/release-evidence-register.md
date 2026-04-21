# Release Evidence Register

Use this register to reference release-candidate and final-release evidence. It keeps `docs/release-golden-path.md` actionable without hard-coding one temporary workflow run into process documentation.

## Current Evidence Slots

| Evidence | Required for RC | Required for final | Source |
| --- | --- | --- | --- |
| Candidate commit SHA | yes | yes | `git rev-parse HEAD` |
| RC validation workflow run | yes | no | `.github/workflows/nightly-rc-validation.yml` with `validation_depth=release-candidate` |
| Release workflow run | no | yes | `.github/workflows/release.yml` |
| Release verification report | yes | yes | `release-verification-report.json` artifact |
| Release candidate image report | image RCs only | image releases only | `release-candidate-verification-report.json` artifact |
| SBOM | yes | yes | `release-sbom.json` artifact |
| Checksums | yes | yes | `release-assets/SHA256SUMS` |
| Release notes | no | yes | GitHub Release notes or `CHANGELOG.md` entry |
| Operations handoff | yes | yes | Release decision record |
| Product benchmark suite version | yes | yes | `docs/product-benchmark-suite.md` and `agent.product_benchmark_suite.build_product_benchmark_suite` |
| Product benchmark evidence run | yes | yes | `docs/product-benchmark-first-run.md` or CI/release benchmark artifact |
| Benchmark narrative fields | yes | yes | `headline`, `best_signal`, `governance_signal`, `regression_watch`, `evidence_links` from the product benchmark suite |
| Comparative target notes | no | yes | `openhands-like`, `opendevin-like` and `openclaw-like` comparison targets in `docs/product-benchmark-suite.md` |

## Decision Record Template

```markdown
Release:
Commit:
Candidate or final tag:
RC validation workflow run:
Release workflow run:
Release verification report artifact:
Release candidate verification report artifact:
SBOM artifact:
Checksum artifact:
Release notes:
Product benchmark suite version:
Product benchmark evidence run:
Benchmark headline:
Benchmark best signal:
Benchmark governance signal:
Benchmark regression watch:
Benchmark evidence links:
Rollback path:
Decision:
```

## Completion Rule

`PRD-022` is considered operationally satisfied when a release candidate or final release points to this register with populated workflow-run and artifact links. Until then, the process is defined and ready, but no concrete release has been approved through it.
