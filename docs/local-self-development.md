# Local development with an eGPU and TypeSafe Jev

The Hub owns task ingestion, dispatch, budgets, verification and publication.
Workers execute delegated tasks in an isolated development checkout. Mount the
running application source read-only and expose only the task workspace for writes.

Use a local code model through a registered `llamacpp` backend. The canonical
endpoint policy accepts the Docker aliases `llamacpp` and `llama-cpp`; arbitrary
Docker service names are rejected. Give each model slot its configured context
window and check actual GPU inference before starting work.

TypeSafe Jev is a separate external decision provider, not the code generator.
Mount a dedicated key file read-only and set `TYPESAFE_API_KEY_FILE` on the Hub
and participating Workers. Enable `external_calls_allowed` explicitly. Decision
results remain proposals; existing tool and approval policies still apply.

## Task-specific proposal backends

The Autopilot defaults to `autopilot_no_human_review` proposal strategy mode.
For coding tasks that should use their existing task-kind backend and workspace
tool loop, configure:

```json
{
  "autopilot": {
    "async_dispatch_enabled": false,
    "strategy_mode_override": "task_default"
  }
}
```

`task_default` omits the request-level strategy override. Task-kind routing and
the existing task proposal implementation select the backend. It does not grant
write access, suppress review, or disable admission checks. Existing configurations
retain their previous default. Restart the Hub after upgrading this routing code.

The existing CLI workspace executor can be selected in a delegated Worker with
`worker_runtime.native_worker_runtime.enabled=false`. The configuration API
preserves this explicit boolean and validates its `sgpt` fallback. This setting
does not authorize execution in the Hub. Keep the Hub-to-Worker assignment and
container boundary in place; the native command-plan path requires its own
dedicated Worker integration.

Before enabling workspace mutation, configure an exact writable file scope and
an allowlisted, bounded test command. Keep the materialization requirement,
protected paths, audit and approval lifecycle in place. Start the Hub Autopilot
for one dedicated team or goal with explicit concurrency, runtime and dispatch
limits. Failed checks must produce a bounded failed/blocked result.

For the CLI workspace mutation loop, the inline prompt and the generated
`.ananta/response-contract.md` use the same inner `kind` protocol. A successful
CLI loop returns the single Hub-allowlisted verification command as the outer
proposal; the normal execute path still authorizes and runs that command.
Configure exactly one verification command for this path. Missing or ambiguous
commands, invalid model output, policy blocks and exhausted loop budgets return
a nonzero result. A failed Worker loop is never repaired into a fresh command.

The existing prompt builder still combines context preparation and prompt
assembly (SRP debt). The context writer accepts an optional contract as text,
so it does not depend on a concrete CLI backend (DIP). No new task scheduler,
approval bypass or Worker-to-Worker delegation is introduced.

For the standalone corpus pilot, isolate its tests from unrelated Hub bootstrap
fixtures and keep pytest's cache outside the source workspace:

```sh
python -m pytest -q --confcutdir=tests/research_training -o addopts= -o cache_dir=/tmp/ananta-growth-pilot-cache tests/research_training/test_growth_corpus.py tests/research_training/test_growth_corpus_boundaries.py
```

This command covers only the standalone corpus tests, which need no Hub test
fixtures. Register the exact command in the Hub allowlist. It does not replace
the broader regression suite or production release gates. This prevents runtime
cache and unrelated bootstrap output from being mistaken for scoped source edits.

## Publication boundary

Do not enable the legacy Workspace Git auto-push path as a substitute for Hub
verification: it can publish before the Hub's final verification completes.
An isolated working branch and successful inference do not establish a complete
autonomous publication pipeline. Commit and push must remain blocked until a
Hub-owned publication implementation binds the verified task, exact change set,
tests, repository and destination branch to its authorization. User permission
to automate publication does not make an unverified change set valid evidence.

SRP/DIP: this configuration reuses the existing proposal and backend boundaries;
it introduces no additional scheduler or Worker orchestration loop. Existing
legacy proposal/execution coupling remains and needs a separate migration.
