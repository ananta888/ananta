# KRITIS Audit: Canonical Schema and Hooks

This document defines the canonical audit schema and the baseline hook coverage for KRITIS audit tasks T02-T06.

## Canonical schema (`canonical_audit_event.v1`)

Required fields:

- `trace_id`
- `task_id`
- `actor`
- `role`
- `policy_version`
- `operation_type`
- `target`
- `outcome`
- `timestamp`

Additional chain and context fields:

- `chain.parent_trace_id`
- `prompt_bundle_class`
- `context_classes`
- `details`

## Middleware coverage

A central HTTP middleware emits:

1. `http_request_started`
2. `http_request_completed`

for API surfaces (`/api*`, `/v1/mcp*`), including route target metadata and duration/status outcome.

## LLM interaction hooks

LLM interceptor requests emit auditable interaction metadata:

- provider/backend target
- model
- policy version
- prompt bundle class
- context classes
- trace/task linkage

Prompt content remains metadata-first and redaction-aware.

## Prompt/context class design

Prompt/context logging is modeled as classes, not raw payload dumps:

- `prompt_bundle_class` for prompt contract class
- `context_classes` for bounded context types (e.g. `repo`, `artifact`, `task_memory`, `wiki`)

This supports incident reconstruction while avoiding unnecessary sensitive content storage.

## Tool call chaining

Tool calls are linked through:

- `trace_id` for current request
- `chain.parent_trace_id` for parent LLM/request chain
- `target.tool_name` and scoped argument metadata

This enables correlated multi-step audit review across LLM -> tool execution paths.
