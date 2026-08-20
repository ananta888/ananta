# Tiny Tool Router

## Decision

Ananta integrates tiny action models as candidate-only adapters in front of
the existing worker LLM loop. The Hub remains the orchestrator. A tiny model
may name one tool and propose arguments, but it cannot register, authorize or
execute a tool.

The integration is additive and disabled by default. The modes are disabled,
shadow and active. Shadow evaluates the same candidate path but always sends
the request to the established main model and never executes the shadow
candidate.

## Component map

| Concern | Existing source of truth | Tiny-router decision |
| --- | --- | --- |
| Tool inventory | agent/services/ananta_tool_registry_service.py | Reuse without a second registry |
| Canonical schemas | agent/services/tool_schema_adapter_service.py | Reuse OpenAI projection, then apply loss-aware dialect adapters |
| Calling mode | agent/services/tool_calling_mode_service.py and model profiles | Reuse provider capabilities |
| Provider transport | agent/services/model_invocation_service.py | Reuse through ModelInvocationTransport; no new HTTP client |
| Policy | agent/services/ananta_tool_policy_service.py | Remains authoritative after candidate validation |
| Execution | agent/services/unified_tool_execution_service.py | Sole gateway for accepted tiny candidates |
| Orchestration | agent/cli_backends/tool_loop.py | One optional first-iteration fast path; main loop owns fallback |

This separation protects SRP: profiles, schema projection, untrusted-output
validation, model transport, preselection, observability and orchestration
have focused modules. DIP is preserved because the router depends on
candidate-adapter and telemetry ports. It intentionally has no executor port.

## Flow and invariants

1. The worker loop determines its already allowed tool names.
2. ToolSchemaAdapterService exports only those canonical descriptors.
3. The router removes risk classes that are not allowed for the configured
   fast path. Read-only is the default.
4. Deterministic preselection reduces that allowed subset to top-k.
5. A configured profile and adapter produce untrusted candidate data.
6. CandidateValidator checks exact tool identity, duplicate keys, one-call
   cardinality, confidence and argument types and constraints.
7. Active mode returns a candidate. Shadow mode records only metadata.
8. The worker loop evaluates its existing policy and sends an allowed tiny
   candidate through UnifiedToolExecutionService exactly once.
9. Every failure, abstention, deadline or low confidence escalates to the
   existing main model with a machine-readable reason.

Unknown profile schema versions, empty allowed scopes, unknown tool names and
unsupported runtime dependencies fail closed. No cloud handoff is automatic.

## Capability and profile contract

The catalog is config/models/tiny_action_model_profiles.v1.json and is
validated against config/schemas/tiny_action_model_profiles.schema.json.
Every profile declares model and adapter IDs, tier, schema dialect, licensing,
commercial/research restrictions, context and tool limits, confidence support
and an optional artifact hash. All candidates are disabled by default.

An unknown catalog version or malformed profile puts the loader into safe
mode with zero runnable profiles. Deployment must pin a model artifact hash
before an active production rollout; the empty value in candidate profiles is
therefore a deliberate block on treating research metadata as deployed bits.

## Transport decision matrix

| Candidate | Adapter | Schema path | Transport decision | Default |
| --- | --- | --- | --- | --- |
| Needle 2 | needle | Canonical OpenAI to raw Needle schemas | Optional local cactus-needle complete call; never Needle run | Off |
| FunctionGemma 270M | openai_compatible | FunctionGemma and OpenAI declarations | Existing ModelInvocationService | Off |
| LFM2.5 350M and 1.2B | openai_compatible | OpenAI tools | Existing ModelInvocationService | Off |
| xLAM 1B | openai_compatible | Explicitly loss-reported xLAM prompt projection | Existing text invocation | Research-only and commercially denied |

The generic OpenAI-compatible path is preferred because Ananta already owns
provider resolution, credentials, retries and response handling there.
Adapter code must not instantiate requests, httpx, OpenAI or provider SDK
clients.

## Escalation

Configured profile order defines Tiny then Small attempts. max_hops is capped
at three and max_total_ms is capped at thirty seconds. Reasons include
dependency missing, adapter unavailable, timeout, invalid JSON, unknown or
denied tool, argument schema failure, confidence missing, below threshold and
license denial. Exhaustion escalates to Main, which is the unchanged worker
LLM path.

Only iteration one is eligible. This prevents the same initial request from
being proposed again after a ToolResult and is the structural exactly-once
guard in addition to the unified execution gateway.

## Schema dialect policy

OpenAI and Needle projections retain the canonical parameter schema. xLAM's
published compact example represents properties but not required and
additional-properties constraints; the adapter reports those losses and the
post-model validator always reapplies the full canonical schema. No adapter
may invent missing semantic values. Syntax handling is limited to strict JSON
and an optional whole-response JSON fence.

## Sources

- Needle repository and Apache-2.0 code license:
  https://github.com/cactus-compute/needle
- Needle complete, confidence, retrieval and offline runtime contract:
  https://github.com/cactus-compute/needle/blob/main/doc/apis.md
- FunctionGemma overview and intended specialization:
  https://ai.google.dev/gemma/docs/functiongemma
- FunctionGemma tuning guide:
  https://developers.googleblog.com/a-guide-to-fine-tuning-functiongemma/
- LFM2.5-350M tool-calling and context documentation:
  https://docs.liquid.ai/lfm/models/lfm25-350m
- xLAM output format and noncommercial research license:
  https://huggingface.co/Salesforce/xLAM-1b-fc-r

External benchmark numbers are not release gates. Only measurements produced
with Ananta's committed cases, exact profile and artifact hash and runtime
environment may justify rollout.
