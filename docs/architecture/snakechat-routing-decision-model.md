# SnakeChat Routing Decision Model (SCTR)

## Overview

SnakeChat messages typed in the TUI operator surface take one of several execution paths.
The routing decision is made once per message, before any LLM call, by the
`SnakeChatCommandRouter`.

---

## Decision Tree

```
User types a message in the TUI chat bar
  │
  ├─ Starts with "/" or is a known slash-command
  │       → CommandDispatch: execute locally (no LLM)
  │
  ├─ Plain natural-language question
  │       │
  │       ├─ SnakeChatCommandRouter.route(question)
  │       │       │
  │       │       ├─ FilesystemReadTool intent detected
  │       │       │     → FilesystemReadTool.execute() [local, no LLM]
  │       │       │
  │       │       ├─ Git/todo read intent
  │       │       │     → GitReadTool / TodoReadTool [local, no LLM]
  │       │       │
  │       │       └─ Requires reasoning / context
  │       │             → /snake/ask (LLM path)
  │       │                   │
  │       │                   ├─ v1: {question, context, depth}
  │       │                   ├─ v2: v1 + memory_context
  │       │                   └─ v3: v2 + candidate_files + context_files (CWFH)
  │       │
  │       └─ Routing bypassed (direct_llm flag)
  │             → direct LMStudio call from TUI
  │
  └─ Notes (local_only)
          → Never enters routing; stored in ChatMemory only
```

---

## Components

### SnakeChatCommandRouter (SCTR-004)

`client_surfaces/operator_tui/snake_chat_command_router.py`

Makes a lightweight LLM call (small model) to classify intent into one of:
- `"filesystem_read"` — list/read local files
- `"git_read"` — git log/status/diff
- `"todo_read"` — read ananta todo JSON files
- `"llm_answer"` — needs reasoning; route to `/snake/ask`
- `"direct_answer"` — simple factual; answer inline without LLM

Fallback: when the classifier fails or is disabled, defaults to `"llm_answer"`.

### FilesystemReadTool (SCTR-003)

`client_surfaces/operator_tui/tools/filesystem_read_tool.py`

Safe read-only filesystem operations for the SnakeChat surface:
- `list_dir(path)` — directory listing with metadata
- `list_root_files(pattern)` — glob from workspace root
- `read_file(path)` — read a single file (policy-checked)

Security constraints (SCTR-002):
- Only reads from `workspace_root` (no `..` traversal)
- Blocked extensions: `.env`, `.key`, `.pem`, binary blobs
- Max file size: 64 KB for inline display
- No write, no execute, no network access from this tool

### GitReadTool (SCTR-005)

`client_surfaces/operator_tui/tools/git_read_tool.py`

Read-only git operations via subprocess:
- `git log --oneline -20`
- `git status --short`
- `git diff --stat HEAD~1`
- `git show <sha> --stat`

All invocations are allowlisted; no arbitrary git commands.

### TodoReadTool (SCTR-006)

`client_surfaces/operator_tui/tools/todo_read_tool.py`

Read ananta todo JSON files from `todos/` directory:
- List all todo files
- Read specific track (e.g. `cwfh`, `amr`, `epc`)
- Filter by status (`todo`, `in_progress`, `done`, `blocked`)

---

## Security Policy (SCTR-002)

`client_surfaces/operator_tui/snake_chat_security_policy.py`

| Rule | Enforcement |
|------|-------------|
| No write operations | Router only dispatches read-classified intents to tools |
| No shell execution | All tool paths use explicit Python APIs, not subprocess for reads |
| No cross-workspace reads | `workspace_root` boundary enforced in FilesystemReadTool |
| No secrets in LLM context | Strip known secret patterns before sending to `/snake/ask` |
| No arbitrary code in tool input | Tool arguments are validated against allowlisted patterns |
| Note messages never leave TUI | `local_only` visibility flag blocks ChatTransport enqueue |

### Context Sanitization

Before sending context to `/snake/ask`, the `SnakeChatSecurityPolicy` strips:
- Environment variable assignments matching `[A-Z_]+=\S+` patterns
- PEM blocks
- Token-like strings (>30 chars, no spaces, mixed case/numbers)

---

## /snake/ask Version Selection (CWFH-008)

The TUI backend selects the highest supported handoff version:

```
try v3 → POST /worker-context + POST /snake/ask with worker_v3_payload
    ↓ (404 or error)
try v2 → POST /snake/ask with memory_context
    ↓ (error)
v1  →  POST /snake/ask with {question, context, depth}
```

Version capability is probed once at connect time and cached.

---

## Adaptive Routing (SCTR-007)

The router records per-question-type latencies and error rates.
If the classifier LLM is slow (>3s) or frequently wrong, it falls back to
a keyword-based heuristic router until the LLM recovers.

---

## Telemetry (SCTR-008)

Each routing decision emits a telemetry record:
```json
{
  "question_hash": "sha256[:8]",
  "route": "llm_answer",
  "classifier_latency_ms": 120,
  "used_v3_handoff": true,
  "candidate_count": 12,
  "context_files_read": 3
}
```

These are written to `logs/snakechat_routing.jsonl` and surfaced in the
diagnostics tab of the TUI.

---

## File Map

| File | Role |
|------|------|
| `client_surfaces/operator_tui/snake_chat_command_router.py` | Intent classifier + route dispatch |
| `client_surfaces/operator_tui/snake_chat_security_policy.py` | Security rules for tool use and context |
| `client_surfaces/operator_tui/tools/filesystem_read_tool.py` | Local filesystem read tool |
| `client_surfaces/operator_tui/tools/git_read_tool.py` | Git read tool |
| `client_surfaces/operator_tui/tools/todo_read_tool.py` | Todo JSON read tool |
| `client_surfaces/operator_tui/chat_prompt_builder.py` | Builds v1/v2/v3 payloads |
| `agent/routes/snakes.py` | `/snake/ask` and `/worker-context` endpoints |
| `worker/retrieval/codecompass_candidate_resolver.py` | Candidate scoring for v3 |
| `agent/services/context_file_reader_service.py` | Policy-checked file reads |
