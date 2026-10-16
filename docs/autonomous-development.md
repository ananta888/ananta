# Autonomous TODO development

The opt-in Hub development service reads tracked, non-archived TODO JSON files,
imports open tasks into the existing Hub task system, delegates coding and tests
to Workers, and publishes verified changes to an `ananta/` development branch.
The Hub controls queueing, dependencies, budgets, evidence and Git publication.
Workers receive Git-free source exports and no GitHub publication credential.
The service is disabled unless `ANANTA_SELF_DEVELOPMENT_POLICY_FILE` names an
explicit Hub-private operator policy.

## Operator commands

From the repository root:

```sh
python3 scripts/ananta-self-develop.py import
python3 scripts/ananta-self-develop.py start
python3 scripts/ananta-self-develop.py status
python3 scripts/ananta-self-develop.py stop
```

For a new task, get the form, edit it, submit it and start execution:

```sh
python3 scripts/ananta-self-develop.py form > /tmp/my-ananta-task.json
# Fill in title, description, files, test_profile and commit_message.
python3 scripts/ananta-self-develop.py submit /tmp/my-ananta-task.json
python3 scripts/ananta-self-develop.py start
```

By default the client shows counts, blocking reasons and active/published tasks.
Use `status --details` for every imported record.

The client reads `ANANTA_HUB_TOKEN_FILE` or `--token-file`. On the local developer
deployment it also recognizes the existing token below
`~/ananta-data/workflow-runtime-dev/hub/`. `--url` or `ANANTA_BASE_URL` selects the
Hub. Tokens never appear in command arguments or output. Equivalent admin-only
endpoints are `/api/self-development/{form,import,start,stop,status}`. Import with
an empty body scans the tracked TODO catalog; `{"document": ...}` submits a form.

Minimal task:

```json
{
  "tasks": [{
    "id": "MY-001",
    "title": "Cover the new behavior",
    "description": "Describe exactly what should change and how to verify it.",
    "status": "todo",
    "depends_on": [],
    "execution": {
      "files": ["tests/self_development/test_example.py"],
      "test_profile": "self-development",
      "commit_message": "test(autopilot): cover the requested behavior",
      "completion_rule": "profile_passed"
    }
  }]
}
```

`test_profile` references an operator-approved exact command, not arbitrary model
shell output. The same command must be registered in the Workers' existing test
allowlist. Existing mutation scope, approval and materialization checks still
apply. A task is eligible only when its exact files fit the operator policy.
For an existing TODO add the same `execution` block to that task. Import is
idempotent by source file and task ID; execution contracts can be added to a
previously blocked import. Source tasks already marked blocked, paused or needing
review remain blocked. Missing contracts, unknown profiles and unpublished
prerequisites return explicit reasons. Same-document dependencies release only
after the prerequisite has been published. Missing/cyclic dependencies remain
bounded blocked records; they do not cause an execution loop. The Hub queue
holds ineligible imports as `paused`, because generic dependency reconciliation
may otherwise release a `blocked` task with no known dependency edges. Cross-document
references need an explicit execution plan.

## Operator policy and publication

The policy is outside Worker mounts, owned by the operator/Hub, and includes:

- `enabled: true`, `automatic_publication: true`;
- fixed `remote_url: https://github.com/ananta888/ananta.git` and `branch: ananta/self-development`;
- private `control_root`, separate shared `workspace_root`, `github_token_file`;
- `team_id`, `tenant_id`, `allowed_roots`, `max_tasks` (1–50), `max_seconds` (at most six hours);
- `test_profiles`, mapping names to exact commands and bounded timeouts.

Initialize `control_root/checkout` from the trusted source revision, configure its
origin, development branch, upstream and local commit identity. Mount that private
directory and the credential file only in the Hub. Do not register it as a public
Ops workspace. No Worker may write the policy, journal, sealed artifacts or Git
metadata. The setup does not deploy generated changes to the running application
or merge into main.

Before delegation the Hub persists an immutable source manifest and registers
its `SRC_*`/`RUN_*` through the existing Hub Evidence Registry. Workers receive only
the closed assignment. After execution the Hub requires the assigned Worker,
completed status, exit code zero and exact test-profile command. It seals regular
source files, rejects undeclared edits/deletions/symlinks/oversized files, and
checks registry bindings again before each Git operation. Test/runtime caches and
Hub-generated context are excluded from publication. Evidence is `local` scope;
it cannot satisfy production release gates.

For tracked TODO sources, the Hub updates only the matched task's completion
metadata after the successful evidence gate and commits it together with the
verified code change. `done` means the explicitly declared `profile_passed`
contract was fulfilled. Broader production acceptance is not implied.

GitOps performs explicit-path staging, conventional commit validation, audited
mutations and a non-force push to the registered development upstream. A narrow
pre-authorization binds each stage/commit/push to the verified files, message,
workspace and destination. The generic Ops approval default stays unchanged.
Mandatory `human_required_tools` and explicit Ops denials still reject automatic
publication; the operator must deliberately authorize this headless development
profile in the Hub policy. Worker Git remains disabled.

Transport uses the existing short-lived Hub credential capability and remote
policy, certificate verification, public-IP pinning, no redirect/proxy/submodule
or LFS fallback. A certificate-validated fixed public DoH endpoint is used only
when local GitHub DNS fails. Credentials are neither persisted in Git config nor
sent to Workers. Push failures retain the committed SHA in the journal; a failed
publication never counts as completed development. Worker execution failures
may receive at most two attempts by default (`max_attempts`, capped at three).
Each retry creates a fresh Task, workspace, assignment and registered run; the
failed attempt is retained. Stop signals, exhausted budgets and failed
publication do not trigger a new coding attempt. The journal records bounded
failures rather than waiting for human approval. `stop` immediately revokes
publication even while a Worker is still running; a new `start` creates a new
bounded session. Operator policy changes revoke the cached session.

## Verification and structure

```sh
python -m pytest -q --confcutdir=tests/self_development -o addopts= -o cache_dir=/tmp/ananta-self-dev-cache tests/self_development
```

The contract/catalog, private journal, source export/sealing, completion metadata,
credential transport and publication policy are separate collaborators (SRP).
The existing Hub Autopilot invokes the opt-in coordinator; other tasks retain
their execution path (OCP). Evidence, queue and publication dependencies can be
injected for headless tests (DIP). Existing legacy proposal/execution coupling
and configuration-level Worker mutation scope remain architectural debt; this
change does not migrate that subsystem. There is no new Worker scheduler or
Worker-to-Worker orchestration.
