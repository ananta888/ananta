# Git, Docker and Compose Ops Control Surfaces

## Decision

Ananta exposes Git, Docker and Docker Compose operations through a hub-side Ops facade.
Angular `/operations`, Operator TUI `:ops`, and agent tools consume the same `/api/ops`
contract. Browsers and TUI adapters do not add their own shell logic.

The first supported mode is read-only diagnostics. Mutating actions are represented in
the backend contract, but they are controlled by `ops_policy`, admin routes, approval
state and audit. Dangerous actions such as force-push, pruning, volume deletion and
`compose down --volumes` are denied by default.

## Docker Boundary

Default boundary: `disabled`.

With the default configuration, Docker endpoints return `docker_boundary_not_configured`.
The hub does not assume `/var/run/docker.sock`, does not mount a Docker socket in the
standard Compose files, and does not silently control host Docker.

To enable local diagnostics, set:

```yaml
docker_ops:
  boundary: hub_cli
```

Expected tools for `hub_cli`:

- `docker`
- `docker compose`
- A configured Docker context or `DOCKER_HOST`
- OS permissions for the hub process to call Docker

Platform notes:

- WSL2: ensure Docker Desktop WSL integration is enabled for the distro running the hub.
- Linux: the hub user needs Docker CLI access; avoid adding broad group permissions without review.
- macOS/Colima: start Colima and ensure the Docker context is visible to the hub environment.
- Docker Desktop: verify the CLI context with `docker context ls` before enabling the boundary.

## Existing Logic Matrix

| Area | Read-only | Mutating | Direct subprocess | Policy/Audit | Direction |
| --- | --- | --- | --- | --- | --- |
| `agent/tools_git.py` | status, diff, log | commit, push | yes | action-pack check, partial audit | Keep compatible; prefer namespaced `git.*` tools. |
| `agent/services/workspace_git_service.py` | workspace context | add `-A`, commit, push | yes | none inside service | Preserve worker git sync path; do not reuse for user Ops mutations. |
| `client_surfaces/operator_tui/tools/git_read_tool.py` | branch, status, commits | no | yes | no | Compatibility fallback only; new TUI Ops uses `/api/ops`. |
| `client_surfaces/operator_tui/diff/diff_source_resolver.py` | diff/file reads | no | yes | resolver-level checks | Keep for diff viewer; not a general Ops facade. |
| `frontend-angular/src/app/components/operations-console.component.ts` | task read-model | task claim/complete | no shell | backend auth | Extended in place with Ops tabs. |
| `docker/compose-next` | preferred Compose definitions | by external CLI | n/a | n/a | Registered as preferred read model source. |
| `docker/old_way` | legacy Compose definitions | by external CLI | n/a | n/a | Kept visible as legacy; no deletion in this track. |
| `docs/compose-profiles.md` | profile docs | no | n/a | n/a | Linked from this Ops runbook. |

Deprecated direction: direct Git shell helpers in UI/TUI-specific code should not grow
new behavior. New read-only and mutating flows should enter through
`agent.services.git_ops_service`, `docker_engine_service`, `docker_compose_service` and
`/api/ops`.

## Backend Contract

Read endpoints:

- `GET /api/ops/git/status?workspace_id=repo`
- `GET /api/ops/git/diff?workspace_id=repo&path=...&cached=false`
- `GET /api/ops/docker/status`
- `GET /api/ops/docker/containers`
- `GET /api/ops/docker/containers/{id}/logs?tail=200`
- `GET /api/ops/compose/projects`
- `GET /api/ops/compose/projects/{id}/status`
- `GET /api/ops/compose/projects/{id}/logs?service=...&tail=200`

Mutation endpoints exist as admin-only policy-gated contracts:

- `POST /api/ops/git/stage`
- `POST /api/ops/git/commit`
- `POST /api/ops/git/push`
- `POST /api/ops/docker/containers/{id}/action`
- `POST /api/ops/compose/projects/{id}/action`

Stable error codes include `workspace_not_allowed`, `path_not_allowed`,
`approval_required`, `policy_denied`, `docker_boundary_not_configured` and
`output_truncated`.

## Compose Read Model

`docker/compose-next` is registered as preferred. `docker/old_way` is registered as
legacy. The read model classifies definitions into purpose categories where possible:
`prod`, `dev`, `lite`, `oidc`, `ci`, `e2e`, `tests` and `public-rendezvous`.

This track does not remove `docker/old_way` and does not change CI, quickstart or E2E
Compose invocation paths. Follow-up migration tasks should be separate, scoped changes.

## Operator Workflow

Angular:

- Open `/operations`.
- Use the Ops Control Surface tabs for Git, Docker and Compose status.
- Docker unavailable states are shown from backend error codes.

Operator TUI:

- `:ops status` loads Git, Docker and Compose snapshots.
- `:ops git`, `:ops docker`, `:ops compose` load focused snapshots.
- The command stores the hub response in the `ops` section payload.

Instructor/operator mode:

- Use Git dirty status to check whether a participant workspace has uncommitted work.
- Use Docker status to distinguish `docker_boundary_not_configured`, missing CLI and unreachable daemon.
- Use Compose project visibility to verify whether preferred or legacy stacks are present.

## Troubleshooting

| Symptom | Likely code | Check |
| --- | --- | --- |
| Docker tab says boundary not configured | `docker_boundary_not_configured` | Confirm this is expected default or set `docker_ops.boundary=hub_cli`. |
| Docker CLI missing | `docker_not_found` | Install Docker CLI in the hub execution environment. |
| Docker daemon unreachable | `docker_unreachable` | Check Docker Desktop/Colima/daemon and Docker context. |
| Permission denied | `docker_permission_denied` | Check hub user Docker permissions and socket/context access. |
| Compose status fails | `compose_plugin_missing` | Verify `docker compose version`. |
| Workspace rejected | `workspace_not_allowed` | Use a registered `workspace_id`; do not pass raw paths. |
| Path rejected | `path_not_allowed` | Use workspace-relative paths; traversal and symlink escape are denied. |
