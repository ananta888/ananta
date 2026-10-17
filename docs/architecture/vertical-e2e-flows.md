# Vertical E2E Flows

## Scope

This document describes the vertical end-to-end flows that connect workspace operations with GitHub integration and rollback semantics.

## Flow: Workspace + GitHub Rollback

1. A worker or agent performs a workspace mutation (e.g., file write, patch).
2. The mutation is staged in the local workspace directory.
3. The system pushes the change to the associated GitHub repository via the source-control integration.
4. If the push or downstream validation fails, the rollback path restores the previous known-good state from the local cache or Git history.
5. E2E tests verify that the rollback leaves the workspace in a consistent, verifiable state.

## Test Coverage

- `tests/e2e/test_e2e_workspace_github_rollback.py` exercises the full vertical flow: mutation → push → failure → rollback → verification.
- The test uses a local bare Git repository to avoid external dependencies while still exercising real Git operations.

## Related Artifacts

- `.github/workflows/backend-isolated-flows.yml` — CI job that runs isolated backend flows including rollback scenarios.
- `tests/e2e/test_workspace_git_sync_flow.py` — sibling test covering the happy-path sync flow (commit+push → clone sees files).
