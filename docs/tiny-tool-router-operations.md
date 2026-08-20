# Tiny Tool Router Operations

## Safe defaults

The worker-loop configuration contains tiny_router with mode disabled, an
empty profile order, read-only risk classes and a kill switch. With these
defaults no adapter loads and the existing worker LLM behavior is unchanged.

Example shadow configuration:

    ananta_worker_tool_loop:
      tiny_router:
        mode: shadow
        profile_order: [needle-2-45m, functiongemma-270m]
        top_k: 5
        max_hops: 2
        max_total_ms: 1500
        allowed_risk_classes: [read]
        commercial_use: true

Do not activate a profile with an empty artifact_sha256 in production. First
record the exact artifact, runtime version, hardware class and evaluation
dataset hash in the deployment change.

## Rollout gates

1. Disabled: confirm the old worker-loop behavior and zero adapter calls.
2. Shadow: collect selection accuracy, exact argument match, abstention,
   invalid-schema, escalation, p50/p95 latency and error reason counts.
3. Read-only active: allow only low-risk registry tools and retain the main
   model fallback.
4. Broader evaluation: separately approve any execution or write category.
   The normal policy and approval gates still apply.

Promotion requires zero unauthorized candidate acceptance, zero double
mutation, no prompt or argument content in telemetry, stable latency within
the per-request budget and task-specific quality thresholds. FunctionGemma's
official guidance says task-specific fine-tuning and evaluation are expected;
the base profile is not production proof.

## Kill switch and rollback

Set kill_switch true or mode disabled. Both prevent adapter calls immediately
and restore the existing main-model path. No model or catalog removal is
required, so rollback is additive and does not change API contracts.

Runtime dependency, endpoint, parsing or timeout failures are request-local
and escalate rather than triggering an automatic cloud provider. Repeated
failure rates should be handled by existing operator configuration and
deployment controls, not by hidden worker-side orchestration.

## Licensing

Needle code is Apache-2.0. FunctionGemma uses Gemma terms and is described by
Google as available for responsible commercial use. LFM profiles use LFM-1.0
and still require deployment review. xLAM-1b-fc-r is CC-BY-NC-4.0 with
additional DeepSeek terms and is therefore blocked for commercial mode and
marked research-only.

Licensing metadata is a deployment guard, not legal advice. Recheck upstream
terms when changing a model revision.

## Reproducible checks

Run:

    python -m pytest -q tests/services/tiny_router
    python scripts/run_tiny_tool_router_benchmark.py --check
    python scripts/check_tiny_tool_router.py --tests-passed

The deterministic replay cases validate plumbing and safety. Live model
evaluation must use the same case schema and store JSON and CSV results with
model ID, artifact hash, dataset hash and environment metadata. Training and
evaluation splits use sha256(case_id) modulo five, so reruns cannot silently
reshuffle examples.

Dataset ingestion rejects field names containing credential, secret, token,
password, authorization or api_key. Training data and telemetry must not
contain user prompts, tool results, credentials or raw arguments unless a
separate, approved data-governance process explicitly permits them.

## Failure injection

Acceptance tests cover missing dependencies, adapter timeout, invalid JSON,
unknown tools, wrong argument types, unknown arguments, duplicate calls,
confidence abstention, license denial, empty allowed scope, kill switch,
shadow non-execution and active exactly-once execution through the unified
gateway. CodeCompass is represented through its central registered schema;
the MCP surface does not gain a separate execution route.
