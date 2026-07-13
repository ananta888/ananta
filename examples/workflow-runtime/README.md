# Runtime-neutral workflow example assets

These files form one deterministic, credential-free example:

- `execution-plan.v1.json` — the same Hub-compiled plan for Native, LangGraph
  and Temporal;
- `fake-provider.v1.json` — offline responses; the first draft attempt fails,
  the second succeeds;
- `fake_provider.py` — bounded fixture adapter used by Native, LangGraph and
  the example Hub; it has no network transport;
- `live-provider-profile.v1.json` and `live_provider_probe.py` — optional,
  explicitly local OpenAI-compatible provider probe; credentials are read only
  from a mounted file and LangChain remains a provider adapter;
- `rollout-policy.example.v1.json` — project → tenant → profile → workflow
  narrowing, initially shadow-only;
- `example-manifest.v1.json` — runtime modes, scenario/event expectations,
  dependencies and artifacts.
- `run_example.py` — standalone prepare/resume/evidence runner without a
  pytest dependency;
- `example_hub.py` and `temporal_worker.py` — separated example Hub port and
  real Temporal SDK worker;
- `evidence-schema.v1.json` — machine-readable example-evidence boundary.

Temporal adds durable scheduling and history but consumes the unchanged plan.
LangChain is optional and may only supply provider/retriever/tool adapters. It
is explicitly not a control plane or task-queue owner.

Validate the assets without network access or a test runner:

```bash
python scripts/validate_workflow_runtime_docs.py
```

The executable Compose drill uses the real Native orchestrator, pinned
LangGraph `StateGraph` runtime and a real Temporal server/worker/history. Its
provider, Native ports, LangGraph node executor and Temporal Hub endpoint are
deterministic example doubles. The artifact therefore says `example_only` and
`production_release_gate: false`; it cannot promote a build.

The full fake-provider Compose drill, including a real worker `SIGKILL` and
durable resume, runs in
[`quality-and-docs.yml`](../../.github/workflows/quality-and-docs.yml).
Its teardown step is unconditional. The optional live-provider overlay is
never part of that deterministic CI gate.

The operator walkthrough, Compose commands, failure/approval/cancel/crash/resume
expectations and artifact interpretation are in
[`docs/examples/workflow-runtime/README.md`](../../docs/examples/workflow-runtime/README.md).
Production rollout and recovery controls are in
[`docs/operations/workflow-runtime-rollout.md`](../../docs/operations/workflow-runtime-rollout.md).
