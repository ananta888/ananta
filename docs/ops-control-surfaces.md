# Git, Docker and Compose Ops Control Surfaces

## Decision

Ananta exposes Git, Docker and Docker Compose operations through a hub-side Ops facade.
Angular `/operations`, Operator TUI `:ops`, and agent tools consume the same `/api/ops`
contract. Browsers and TUI adapters do not add their own shell logic.

Read operations are available to authenticated users. Mutations are limited to explicit
registered targets and pass through admin authentication, `ops_policy`, an exact
argument-bound ApprovalRequest and audit. Dangerous actions such as force-push, hard
reset, clean, pruning, image/volume deletion and `compose down --volumes` are not part
of the executable API and remain denied.

## Component Boundaries

| Component | Responsibility | Does not do |
| --- | --- | --- |
| Git | Workspaces, status, changes, three diff scopes, history, branches, redacted remotes, activity and safe mutations | Arbitrary paths/refspecs, force-push, branch deletion, reset/clean |
| Docker | Engine diagnostics, containers, inspect-light, one-shot stats, logs, images, networks, volumes, disk usage and registered lifecycle actions | Arbitrary CLI arguments, secret-bearing inspect fields, prune/delete |
| Compose | Registered projects, multi-file/profile-aware status, normalized config, service logs and registered lifecycle actions | Client-provided files/profiles/project paths, `down --volumes` |
| Logs | Reusable bounded viewer for Docker and Compose stdout/stderr with tail controls and truncation state | Streaming an unlimited log or interpreting log text as commands |
| Approvals | Pending Ops requests, digest/scope display, grant/deny and deliberate exact retry | Automatically executing a grant or reusing it for changed arguments |

The Angular components live under `frontend-angular/src/app/features/operations/`.
`OperationsConsoleComponent` only composes `OperationsSurfaceComponent`; it no longer
contains Git, Docker, Compose, log or approval endpoint logic. Backend shell execution
is isolated behind `CommandRunner`; Angular never constructs shell commands.

## Mutation Flow

```text
Angular / namespaced tool
        -> authenticated Hub route
        -> registered workspace/container/project
        -> Ops policy
        -> pending ApprovalRequest (exact arguments + target digest)
        -> explicit human grant
        -> deliberate retry with approval_id
        -> fixed argv command
        -> one-shot grant consumption + audit event
```

A grant does not run an action automatically. If a path, action, remote, branch,
service or target changes, the digest no longer matches and the retry is denied.

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

For Compose-next, use the explicit boundary overlay. It installs no separate
Angular server and mounts the Docker socket into the hub only:

```bash
ANANTA_DOCKER_OPS_BOUNDARY=hub_cli docker compose --env-file .env \
  -f docker/compose-next/compose.dev.lmstudio.yml \
  -f docker/compose-next/compose.ops-control.yml up -d --build
```

The socket grants host-level container administration. Do not add the overlay
to workers, public deployments, or hubs without enforced admin authentication,
Ops policy, one-shot ApprovalRequests and audit logging.

The overlay also mounts the selected Compose env file read-only at
`/run/ananta/compose.env`. It defaults to the repository `.env`; set
`ANANTA_DOCKER_OPS_ENV_FILE_HOST` to another host path when the running stack
was started with a different env file. The API never returns this file's
contents. Explicit `docker_ops.compose_projects[].env_files` remain restricted
to registered repository files.

Platform notes:

- WSL2: ensure Docker Desktop WSL integration is enabled for the distro running the hub.
- Linux: the hub user needs Docker CLI access; avoid adding broad group permissions without review.
- macOS/Colima: start Colima and ensure the Docker context is visible to the hub environment.
- Docker Desktop: verify the CLI context with `docker context ls` before enabling the boundary.

## Existing Logic Matrix

| Area | Read-only | Mutating | Direct subprocess | Policy/Audit | Direction |
| --- | --- | --- | --- | --- | --- |
| `agent/tools_git.py` | status, diff, log | commit, push | yes | action-pack check, workspace-fingerprinted audit | Compatibility only; prefer namespaced `git.*` tools. |
| `agent/services/workspace_git_service.py` | workspace context | internal add/commit/push | yes | outcome/task/branch/SHA audit without raw paths | Preserve worker sync; expose provenance through Git activity, not as a user mutation adapter. |
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

Git read endpoints:

- `GET /api/ops/git/workspaces`
- `GET /api/ops/git/status?workspace_id=repo`
- `GET /api/ops/git/changes?workspace_id=repo`
- `GET /api/ops/git/diff?workspace_id=repo&path=...&scope=staged|unstaged|combined`
- `GET /api/ops/git/history?workspace_id=repo&limit=50&offset=0`
- `GET /api/ops/git/branches?workspace_id=repo`
- `GET /api/ops/git/remotes?workspace_id=repo`
- `GET /api/ops/git/activity?workspace_id=repo&limit=100`

Docker read endpoints:

- `GET /api/ops/docker/status`
- `GET /api/ops/docker/info`
- `GET /api/ops/docker/containers`
- `GET /api/ops/docker/containers/{id}/inspect`
- `GET /api/ops/docker/containers/{id}/stats`
- `GET /api/ops/docker/containers/{id}/logs?tail=200`
- `GET /api/ops/docker/images`
- `GET /api/ops/docker/networks`
- `GET /api/ops/docker/volumes`
- `GET /api/ops/docker/disk-usage`

Compose read endpoints:

- `GET /api/ops/compose/projects`
- `GET /api/ops/compose/projects/{id}/status`
- `GET /api/ops/compose/projects/{id}/config`
- `GET /api/ops/compose/projects/{id}/logs?service=...&tail=200`

Admin-only, policy- and approval-gated mutation endpoints:

- `POST /api/ops/git/stage`
- `POST /api/ops/git/unstage`
- `POST /api/ops/git/discard`
- `POST /api/ops/git/commit`
- `POST /api/ops/git/fetch`
- `POST /api/ops/git/pull`
- `POST /api/ops/git/push`
- `POST /api/ops/docker/containers/{id}/action`
- `POST /api/ops/compose/projects/{id}/action`

Git path mutations take a non-empty list of workspace-relative paths. Pull is always
`--ff-only`. Push is always the configured remote/branch and never accepts a refspec.
Discard restores only unstaged tracked content: it does not delete untracked files,
does not resolve conflicts and preserves already staged content for the same path.

Container actions are `start`, `stop` and `restart`. Compose actions are `pull`, `up`,
`stop`, `restart` and `down`; a service may be selected only after it was resolved from
the registered Compose config. The server builds all argv arrays.

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
- Git provides selectable registered workspaces, staged/unstaged/untracked/conflict
  status, selective stage/unstage/discard, validated commits, safe synchronization,
  history, branch/upstream divergence, credential-redacted remotes and Ananta activity.
- Docker provides engine capacity, searchable containers, inspect-light, stats, logs,
  images, networks, volumes and storage usage.
- Compose provides project/category/profile visibility, service state, config, logs and
  project- or service-scoped registered actions.
- The shared approval drawer shows the exact request and requires a conscious retry
  after grant. Backend error codes and truncated outputs remain visible.

Git three-way diff:

- Open any changed path and choose **Im 3er-Diff öffnen**.
- Panel A shows `HEAD -> Index` (staged), B shows `Index -> Worktree` (unstaged), and C
  shows `HEAD -> Worktree` (combined).
- Workspace and file filter travel as opaque workspace ID plus relative path. A new
  three-way session preserves this Git preset and its three scopes.
- Diff3 endpoints require authentication and use the same server workspace registry.

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
| Git action asks again after approval | `policy_denied` (reason: `approval_digest_mismatch`) | Retry the unchanged action and selection with the approval ID; changed arguments require a new request. |
| Pull unavailable | `git_dirty_worktree`, `git_operation_in_progress`, `git_no_upstream` | Finish the current Git operation, clean/stage/commit deliberately and configure an upstream. |
| Discard rejected | `git_untracked_discard_denied`, `git_conflict`, `git_path_state_invalid` | Untracked deletion and conflict resolution are intentionally outside this surface. |
| Container action disabled | `docker_container_not_registered` or `policy_denied` | Register the target/Compose project server-side and allow only the required action. |
| Compose config needs variables | `compose_file_invalid` | Mount the matching env file with the explicit Ops overlay or register its server-side env file. |
