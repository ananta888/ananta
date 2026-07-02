# Token Budget and Usage Accounting

## Overview

Ananta gates every LLM/Worker call with a conservative token estimate before the call is made, and normalizes provider-reported usage after it completes. This document explains the architecture, the four context modes, and how to extend or configure the system.

---

## Estimate vs Actual

### Pre-call estimate (`TokenBudgetService.estimate`)

Before any subprocess or API call, the token budget service estimates the prompt size:

1. **tiktoken** (high confidence): If `tiktoken` is installed, the `cl100k_base` encoding is used (approximation for all providers). The raw count is multiplied by `safety_multiplier` (default: 1.25) to account for special tokens and model-specific overhead.
2. **chars_per_token fallback** (low confidence): If tiktoken is not available, the estimate is `ceil(len(text) / chars_per_token * safety_multiplier)`. Default `chars_per_token=4.0`.
3. **empty** (exact): Zero-length input → 0 tokens.

The estimate dict contains: `tokens`, `method`, `safety_multiplier`, `model`, `provider`, `confidence`.

### Post-call normalization (`TokenBudgetService.normalize`)

After a call, the provider's usage block is normalized to a canonical `TokenUsageReport` (see `schemas/chat/token_usage_report.v1.json`):

| Provider  | Input fields                             | Mapped to                      |
|-----------|------------------------------------------|--------------------------------|
| OpenAI    | `prompt_tokens`, `completion_tokens`     | `actual_prompt_tokens`, ...    |
| Ollama    | `prompt_eval_count`, `eval_count`        | `actual_prompt_tokens`, ...    |
| Anthropic | `input_tokens`, `output_tokens`          | `actual_prompt_tokens`, ...    |
| Missing   | (none found)                             | `usage_source: estimate_only`  |

---

## Tokenizer and Fallback

### Per-profile tokenizer strategy

Each `ModelProfile` can specify `tokenizer_strategy`:

- `tiktoken_cl100k` — uses `cl100k_base` encoding (GPT-3.5/4 family)
- `tiktoken_llama3` — uses `cl100k_base` as approximation
- `chars_per_token` — always uses the character-based fallback

The `tokenizer_name` field is available for future direct tokenizer override.

### Cost estimation

If `input_cost_per_1m_tokens` is set on a `ModelProfile`, `estimate_cost_eur(tokens, profile)` in `routing_decision_service.py` returns the estimated EUR cost. Falls back to `price_input_per_million` for backward compatibility.

---

## The Four Context Modes

Context modes are decided by `decide_context_budget()` in `context_budget_policy_service.py` based on classified intent:

### 1. `safe_minimal_chat` (default/smalltalk)

- Triggers: `intent="smalltalk"`, no ModelProfile with `fail_closed=True`, or unknown intent with `fail_closed=True`
- Allowed sources: `user_message`, `short_history`
- Blocked: RAG, CodeCompass, tool schemas, full history, compaction
- Model tiers: `local`, `cheap_cloud`
- Use case: greetings, social messages, quick questions

### 2. `project_chat` (code questions)

- Triggers: `intent="code_question"`, or unknown intent with `fail_closed=False`
- Allowed sources: `user_message`, `short_history`, `rag`, `file_context`
- Blocked: tool schemas, full history, compaction, CodeCompass
- Model tiers: `local`, `cheap_cloud`, `cloud`
- Use case: code questions, debugging, architecture questions

### 3. `tool_enabled_chat` (tool requests)

- Triggers: `intent="tool_request"`
- Allowed sources: `user_message`, `short_history`, `rag`, `tool_schemas`, `file_context`
- Blocked: full history, compaction, CodeCompass
- Model tiers: `local`, `cheap_cloud`, `cloud`
- Use case: agent task execution, file operations, search

### 4. `deep_analysis` (explicit trigger required)

- Triggers: `intent="analysis"`
- Allowed sources: all
- Model tiers: all (including `frontier`)
- `fail_closed=False` — does not default restrictively
- Use case: architecture reviews, benchmarking, comprehensive audits

---

## Default-Deny for Unknown Budgets

When `fail_closed=True` (the default):

- No ModelProfile → `safe_minimal_chat`
- Unknown or empty intent → `safe_minimal_chat`
- Any error in budget service → gate skipped with DEBUG log, never raises

The budget gate in CLI backends (`run_opencode_command`, `run_codex_command`, `run_sgpt_command`) returns `(-1, "", "token_budget_exceeded: ...")` without calling the subprocess when the estimate exceeds `settings.max_prompt_tokens` (default: 128,000).

---

## Context Assembly Trace

Every context assembly session can be traced via `ContextAssemblyTraceService`. The trace:

- Records which sources were included/excluded and their token estimates
- Stores SHA-256 hashes (first 16 hex chars) of content for deduplication
- **Never stores raw prompt text** (`store_prompt_text=True` raises `ValueError`)
- Tracks `truncated_parts`: source types that were excluded in this turn

See `schemas/chat/context_assembly_trace.v1.json` for the full schema.

---

## Configuration

Copy `config/examples/chat_context_budget.yaml` to your config directory and adjust per-mode token limits. Key settings:

| Setting                      | Default | Effect                                              |
|------------------------------|---------|-----------------------------------------------------|
| `fail_closed`                | `true`  | Unknown inputs → most restrictive mode              |
| `safety_multiplier`          | `1.25`  | Multiplied onto raw token estimates                 |
| `store_prompt_text`          | `false` | Must remain false — prompt text is never logged     |
| `store_prompt_hashes`        | `true`  | SHA-256 hashes stored for deduplication             |
| `block_if_estimate_missing`  | `true`  | Block call if estimate cannot be computed           |
