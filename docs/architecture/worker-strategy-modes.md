# Worker Strategy Modes

## Purpose

Ananta supports multiple worker execution styles without changing the hub-owned orchestration model.
A strategy mode selects default propose order, fallback policy, and safety posture.

## Mode Overview

- `opencode_like`
  - Iterative tool loop style
  - Preferred order: `agent_loop_tool_calling` -> `tool_calling_llm` -> `json_schema_llm` -> `flexible_llm_normalization` -> `human_review`
  - Shell denied by default; bounded iteration and tool budgets required
- `codex_cli_like`
  - Patch/command-oriented style
  - Preferred order: `cli_agent_patch_strategy` -> `tool_calling_llm` -> `json_schema_llm` -> `patch_proposal_normalization` -> `human_review`
  - Patch apply is workspace-scoped; risky commands require policy approval
- `hermes_like`
  - Proposal/review-first style
  - Preferred order: `hermes_proposal_strategy` -> `planner_proposal_strategy` -> `json_schema_llm` -> `advisory_proposal` -> `human_review`
  - Non-mutating by default; explicit approval required before apply
- `ananta_native`
  - Deterministic/tool-first style
  - Preferred order: `deterministic_handler` -> `tool_calling_llm` -> `json_schema_llm` -> `worker_strategy` -> `human_review`
  - Strict artifact-first completion and least-privilege defaults
- `openai_compatible_tool_calling`
  - Generic OpenAI-compatible LLM mode
  - Preferred order: `tool_calling_llm` -> `json_schema_llm` -> `flexible_llm_normalization` -> `human_review`
  - Native tool calls first; JSON schema is fallback

## Provider Mapping Semantics

- Native tool-calling capable providers/models:
  - Prefer `tool_calling_llm`
- Providers/models without reliable native tool calls:
  - Use `json_schema_llm` as fallback
- Imperfect structured output:
  - Use `flexible_llm_normalization` as last recovery layer

JSON-only output is not a global requirement. It is required only when the selected strategy is `json_schema_llm`.

## Safety Invariants

- Hub remains control plane and owner of orchestration decisions.
- Workers execute delegated actions only.
- Model text is untrusted input.
- Shell execution from model text is denied by default unless policy explicitly enables it.
- Artifact-first completion is authoritative; model prose is advisory.
