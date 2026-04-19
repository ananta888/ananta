# GitHub Labels And Milestones

Use a small label set that supports triage, release notes and branch-protection decisions.

## Core Labels

| Label | Purpose |
| --- | --- |
| `release-blocker` | Must be resolved before the next release tag. |
| `security` | Security-sensitive work or vulnerability handling. |
| `governance` | Repository, policy, review or architecture governance. |
| `ci` | GitHub Actions, test pipeline or release automation. |
| `flaky` | Unstable test or workflow result tracked separately. |
| `backend` | Backend API, services, persistence or hub logic. |
| `frontend` | Angular frontend or browser-facing behavior. |
| `api` | Public or internal API contract changes. |
| `docs` | Documentation-only or documentation-led changes. |
| `release` | Release process, release notes, artifacts or packaging. |
| `regression` | Behavior that used to work and now fails. |
| `breaking-change` | Compatibility-impacting change. |
| `ignore-for-release` | Excluded from generated release notes. |
| `duplicate` | Duplicate issue or pull request. |
| `invalid` | Not actionable or not applicable. |

## Milestones

Recommended milestones:

- `v1.0.0`: stable release readiness and release blockers.
- `v1.0.0-rc`: release-candidate stabilization.
- `post-1.0`: follow-up hardening that should not block the first stable release.

## Release Notes

`.github/release.yml` groups generated release notes by label. When adding a new release-note label, update that file in the same pull request.
