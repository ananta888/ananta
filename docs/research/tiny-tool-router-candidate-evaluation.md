# Tiny action-model candidate evaluation

## Evidence classification

This document records upstream capability claims separately from Ananta
measurements. Upstream claims establish only evaluation eligibility. They do
not establish production fitness, Ananta accuracy or safe execution.

## Candidate summary

Needle 2 publishes a 45M-parameter, 14 MB engine, structured function_calls,
confidence output, top-five retrieval for larger tool catalogs and offline
inference after the engine is cached. Ananta uses only complete and never run,
because run executes Python functions internally and would bypass the central
policy/executor boundary. Tuned Needle weights report no calibrated confidence
according to the upstream API documentation, so they must abstain unless a
separate Ananta calibration gate is approved.

FunctionGemma is a 270M model specialized for function calling. Google's model
card describes it as a base intended for task-specific fine-tuning rather
than a direct dialogue model. The profile therefore remains evaluation-only
until Ananta-specific ambiguity, relevance and argument tests pass.

LFM2.5-350M is documented with native function calling and a 32K context. The
350M and 1.2B entries share the existing OpenAI-compatible transport and are
separate Tiny and Small profiles so quality and latency can be compared
without code changes.

xLAM-1b-fc-r publishes a strict tool_calls JSON example and a compact schema
conversion. Its model card specifies CC-BY-NC-4.0 plus additional terms and a
research-only purpose. Ananta rejects it in commercial mode and reports
schema-projection losses before applying the canonical schema again.

## Evaluation matrix

Each real-model run must cover:

| Dimension | Required cases |
| --- | --- |
| Registry size | 5, 20, 50 and 100 candidates |
| Surface | Ananta repository, CodeCompass and MCP-backed discovery |
| Selection | exact tool, ambiguity, no relevant tool |
| Arguments | exact match, missing required, wrong type, enum/range, unknown key |
| Adversarial | instruction to ignore allowlist, invented name, duplicate call, malformed JSON |
| Runtime | missing dependency, timeout, malformed response, provider unavailable |
| Safety | policy denial, approval requirement, shadow non-execution, exactly-once mutation |
| Operations | cold/warm p50 and p95, memory, token estimate, escalation rate |

The committed replay benchmark is deterministic and intentionally does not
pretend to measure a live model. A live result must include the catalog hash,
model artifact SHA-256, runtime and quantization, hardware, case dataset hash,
configuration, JSON detail and CSV summary.

## Fine-tuning boundary

Fine-tuning is an offline evaluation activity, never a worker orchestration
loop. Dataset records use stable IDs and deterministic train/evaluation
splits. The evaluation split is held out. Synthetic generation, if approved,
must record its provider and provenance outside the runtime router and must
not include credentials or production prompts.

Promotion thresholds are configured per deployment. The invariant thresholds
are zero unauthorized acceptance and zero execution from shadow mode.
