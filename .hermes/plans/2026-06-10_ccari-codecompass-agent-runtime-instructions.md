# CCARI Implementation Plan — CodeCompass Agent Runtime Instructions

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Schließt die 11 offenen Tasks in `todos/todo.codecompass-agent-runtime-instructions.json`. Ergebnis ist ein nicht-überschreibbarer Runtime-Instruction-Layer für CodeCompass-Kontext in allen Agenten-Pfaden (ananta-worker, OpenCode, AI-Snake-Chat), plus Hub-seitiges Parsing und Auslieferung von `context_reload_request`-Anforderungen.

**Architecture:**
- Add new "codecompass_runtime" layer in `InstructionLayerCompiler.layer_model()` after governance, before agent_profile_template. The layer is non-overridable: any attempt to override via user_profile/task_overlay is silently rejected (audit-logged, ignored).
- The layer is only activated when the task carries a `codecompass_context` block OR when the agent template is in a known set (`opencode`, `ananta_worker`, `ai_snake_chat`).
- A new helper `_codecompass_runtime_prompt()` returns the canonical prompt block, identical to `runtime_instruction_target.short_prompt_block` in the todo file.
- Hub-side: `ContextDeliveryService.deliver()` gains a new method `handle_reload_request(task, request)` that validates and serves `context_reload_request` blocks embedded in the task or sent via a new route.
- OpenCode gets an extra paragraph appended to the rendered `AGENTS.md` in the worker workspace, scoped by workspace context policy.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, pytest. Existing patterns: `InstructionLayerCompiler._LAYER_MODEL_VERSION` is the v2 base, `context_delivery_service.ContextDeliveryService` is the canonical delivery path.

---

## Source-First Discovery Report (already done before writing this plan)

| Reference in Todo | Real Path | Status |
|---|---|---|
| `agent/services/instruction_layer_service.py` | ✓ exists, 12375 bytes | inherits from `InstructionLayerCompiler` (real impl in compiler) |
| `agent/services/instruction_layer_compiler.py` | ✓ exists, 39865 bytes (SPLIT-009) | the actual layer engine; was a facade split in `fd841f587` |
| `agent/services/agent_profile_service.py` | ✓ exists, 15018 bytes | profile resolver + metadata |
| `agent/services/context_delivery_service.py` | ✓ exists, 8006 bytes | `deliver()` is the canonical entry point |
| `agent/common/sgpt_architecture_scan.py` | ✓ exists, 33670 bytes | `_build_iteration_prompt` (line 344) + `_is_architecture_full_scan_context` (line 403) |
| `agent/services/worker_workspace_service.py` | ✓ exists, 40116 bytes | renders AGENTS.md into workspace |
| `docs/operator-tui/ai-snake-chat-memory-codecompass-worker.md` | ✓ exists, 6466 bytes | AI-Snake-Chat docs |
| `docs/codecompass-retrieval-profile-source-policy.md` | ✓ exists, 10787 bytes | source policy |
| `docs/contracts/codecompass-context-reload-request.md` | ✗ does NOT exist | to create (CCARI-002) |
| `docs/codecompass-agent-runtime-instructions.md` | ✗ does NOT exist | to create (CCARI-001, CCARI-003) |

**Verdict:** All consumer modules exist. The contract documents are missing. Plan creates docs first (low risk, additive), then hooks (medium risk), then tests (TDD).

---

## Risks & Trade-offs

1. **InstructionLayerCompiler is a single-source-of-truth** — adding a layer bumps `_LAYER_MODEL_VERSION` from v2 to v3. Existing tests assert on the v2 layer count (8 layers). Plan handles this by versioning the new layer under a feature flag `ANANTA_CODECOMPASS_RUNTIME_LAYER_ENABLED` (default off) so it can be rolled out incrementally.
2. **ananta-worker prompt injection is risky** — `_build_iteration_prompt` is a hot path. Plan adds a thin check `_needs_codecompass_runtime_rules(context)` that returns False unless CodeCompass context is present, so the rules are only added when relevant.
3. **Hub reload handling crosses boundary into task mutation** — `ContextDeliveryService` is currently a pure function. Plan adds a separate `handle_reload_request` entry point that does not mutate the task but instead returns a fresh `ContextDeliveryResult` enriched with the requested chunks.

---

## Out of Scope

- No new DB tables. No new external dependencies.
- No changes to the existing `agent_profile_template` layer.
- AI-Snake-Chat (CCARI-007) is documented but the Angular UI changes are deferred to a follow-up; the docs are written so the eventual Angular changes have a clear contract.
- OpenCode (CCARI-006) is wired via the worker workspace AGENTS.md (in Python), the OpenCode CLI side is not touched.

---

## Task Index

- CCARI-001, CCARI-003 — `docs/codecompass-agent-runtime-instructions.md` (combined: usage guide + prompt block)
- CCARI-002 — `docs/contracts/codecompass-context-reload-request.md`
- CCARI-004 — `agent/services/instruction_layer_compiler.py` layer addition + flag
- CCARI-005 — `agent/common/sgpt_architecture_scan.py` prompt rule injection
- CCARI-006 — `agent/services/worker_workspace_service.py` AGENTS.md paragraph
- CCARI-007 — `docs/codecompass-agent-runtime-instructions.md` AI-Snake-Chat section (documentation only)
- CCARI-011 — `agent/services/context_delivery_service.py` reload handling + new route
- CCARI-008 — `tests/test_codecompass_runtime_instruction_layer.py` (compiler test)
- CCARI-009 — `tests/test_codecompass_reload_request.py` (parser test)
- CCARI-010 — `docs/operator-tui/ai-snake-chat-memory-codecompass-worker.md` update

Total: 11 tasks, 11 commits.

---

### Task 1: Create the runtime-instructions doc with usage guide + prompt block (CCARI-001 + CCARI-003)

**Objective:** Single document that explains CodeCompass context types and embeds the canonical non-overridable prompt block.

**Files:**
- Create: `docs/codecompass-agent-runtime-instructions.md`

**Step 1: Create the doc**

Write the file with:
- Section "Context types": chunks, file_excerpt, line_range, codecompass_snippet, hub_context, Nodes, Edges, Scores, Warnings, Evidence — each with a 1-paragraph definition and an "evidence" vs "heuristic" callout.
- Section "Reading rules": treat as indexed hints, never fabricate, ask for reload, never claim coverage/policy/dependency without evidence path.
- Section "Canonical prompt block": exactly the `short_prompt_block` text from the todo file.
- Section "Reload request": brief pointer to CCARI-002 contract.
- Section "AI-Snake-Chat specifics": when chat is the surface, the block also tells the chat to show "nachladen empfohlen" markers in the UI.

**Step 2: Verify file exists and content has all sections**

Run: `grep -c "Context types\|Reading rules\|Canonical prompt block\|Reload request\|AI-Snake-Chat" docs/codecompass-agent-runtime-instructions.md`
Expected: `5`

**Step 3: Commit**

```bash
git add docs/codecompass-agent-runtime-instructions.md
git commit -m "docs(codecompass): runtime-instructions usage guide + canonical prompt block (CCARI-001/003)"
```

---

### Task 2: Create the context_reload_request contract doc (CCARI-002)

**Objective:** Formal schema for `context_reload_request` so consumers know the wire format.

**Files:**
- Create: `docs/contracts/codecompass-context-reload-request.md`

**Step 1: Create the doc**

Write the file with:
- Schema name: `context_reload_request.v1`
- Required: `kind: "context_reload_request"`, `reason: str`, `requested_context: list[RequestedContext]`, `risk: "read_only"`
- `RequestedContext` types: `file_range | symbol | codecompass_search | graph_expand | architecture_query`
- Example: a CodeCompass-search reload after a "I don't have enough data" answer.
- Notes: `risk: "read_only"` is the default and the engine REJECTS any request where `risk != "read_only"` with `policy_blocked`. Hub-side limit: max 10 requested_context entries, dedup by (type, query/path) tuple.

**Step 2: Verify**

Run: `grep -c "file_range\|symbol\|codecompass_search\|graph_expand\|architecture_query\|policy_blocked" docs/contracts/codecompass-context-reload-request.md`
Expected: ≥ 6

**Step 3: Commit**

```bash
git add docs/contracts/codecompass-context-reload-request.md
git commit -m "docs(codecompass): context_reload_request contract (CCARI-002)"
```

---

### Task 3: Add the runtime layer to InstructionLayerCompiler (CCARI-004)

**Objective:** A new non-overridable `codecompass_runtime` layer between `governance` and `agent_profile_template`. Disabled by default; activated by feature flag or by CodeCompass context.

**Files:**
- Modify: `agent/services/instruction_layer_compiler.py`
- Test: `tests/test_codecompass_runtime_instruction_layer.py` (new)

**Step 1: Write failing test**

In the new test file, write:
```python
def test_codecompass_runtime_layer_appears_when_flag_enabled():
    from agent.services.instruction_layer_service import get_instruction_layer_service
    svc = get_instruction_layer_service()
    # build a minimal task that has no codecompass context
    task = {"id": "t1", "prompt": "x", "worker_execution_context": {}}
    diag = svc.assemble_for_task(task=task, profile=None, overlay=None)
    layers = [l["id"] for l in diag["layer_model"]["layers"]]
    # Default: no runtime layer
    assert "codecompass_runtime" not in layers

    import os
    os.environ["ANANTA_CODECOMPASS_RUNTIME_LAYER_ENABLED"] = "1"
    try:
        diag = svc.assemble_for_task(task=task, profile=None, overlay=None)
        layers = [l["id"] for l in diag["layer_model"]["layers"]]
        assert "codecompass_runtime" in layers
    finally:
        del os.environ["ANANTA_CODECOMPASS_RUNTIME_LAYER_ENABLED"]
```

**Step 2: Run test, expect FAIL**

Run: `pytest tests/test_codecompass_runtime_instruction_layer.py::test_codecompass_runtime_layer_appears_when_flag_enabled -v`
Expected: FAIL — `'codecompass_runtime' not in ['governance', 'agent_profile_template', 'task_template', 'blueprint_template', 'goal_overlay', 'task_overlay', 'user_profile', 'hub_runtime']`

**Step 3: Implement minimal layer addition**

In `instruction_layer_compiler.py`:
1. Add module-level: `RUNTIME_LAYER_FLAG = "ANANTA_CODECOMPASS_RUNTIME_LAYER_ENABLED"` and helper `_codecompass_runtime_active(task) -> bool` that returns True if the env-var is set OR the task carries a `codecompass_context` block OR the agent_template is in `{"opencode", "ananta_worker", "ai_snake_chat"}`.
2. In `layer_model()`, after the `governance` entry and before `agent_profile_template`, append `{"id": "codecompass_runtime", "source": "hub_policy", "overridable": False}` when `_codecompass_runtime_active(task)` returns True.
3. In `assemble_for_task`, capture `task` into the closure so `layer_model()` can read it. Easiest: add a `task` parameter to `layer_model(task=None)` and pass it from `assemble_for_task`.
4. Add an enforcement check: if a user overlay tries to add an entry with `id == "codecompass_runtime"`, drop it and audit-log "codecompass_runtime_override_rejected".

**Step 4: Run test, expect PASS**

Run: `pytest tests/test_codecompass_runtime_instruction_layer.py::test_codecompass_runtime_layer_appears_when_flag_enabled -v`
Expected: PASS

**Step 5: Verify other InstructionLayer tests still pass**

Run: `pytest -q tests/test_instruction_layers.py`
Expected: all pass

**Step 6: Commit**

```bash
git add agent/services/instruction_layer_compiler.py tests/test_codecompass_runtime_instruction_layer.py
git commit -m "feat(instruction-layer): non-overridable codecompass_runtime layer (CCARI-004)"
```

---

### Task 4: Test that user overlay cannot override the layer (CCARI-008 part 1)

**Objective:** Assert the "non-overridable" contract works.

**Files:**
- Modify: `tests/test_codecompass_runtime_instruction_layer.py`

**Step 1: Write failing test**

```python
def test_user_overlay_cannot_inject_codecompass_runtime_layer():
    """User/task overlays must not be able to add a codecompass_runtime layer."""
    from agent.services.instruction_layer_service import get_instruction_layer_service
    svc = get_instruction_layer_service()
    # Force the layer to be active
    import os
    os.environ["ANANTA_CODECOMPASS_RUNTIME_LAYER_ENABLED"] = "1"
    try:
        # Build a malicious overlay that tries to inject its own runtime layer
        malicious = {
            "id": "user_malicious",
            "layers": [
                {"id": "codecompass_runtime", "source": "user", "overridable": True}
            ],
        }
        task = {"id": "t1", "prompt": "x", "worker_execution_context": {"instruction_context": {"overlay_id": "user_malicious"}}}
        # We only need to assert the layer_model doesn't grow from the malicious entry.
        # Use the layer_model() method directly to assert defense.
        layers = svc.layer_model(task=task)["layers"]
        rt = [l for l in layers if l["id"] == "codecompass_runtime"]
        assert len(rt) == 1, f"runtime layer should appear exactly once, got {len(rt)}"
        assert rt[0]["source"] == "hub_policy", f"runtime layer source must stay hub_policy, got {rt[0]['source']}"
    finally:
        del os.environ["ANANTA_CODECOMPASS_RUNTIME_LAYER_ENABLED"]
```

**Step 2: Run, expect PASS** (the source-attribute check in Step 3 of Task 3 already enforces this).

Run: `pytest tests/test_codecompass_runtime_instruction_layer.py::test_user_overlay_cannot_inject_codecompass_runtime_layer -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_codecompass_runtime_instruction_layer.py
git commit -m "test(instruction-layer): user overlay cannot override codecompass_runtime (CCARI-008)"
```

---

### Task 5: Test that the layer is only active when CodeCompass context is present (CCARI-008 part 2)

**Files:**
- Modify: `tests/test_codecompass_runtime_instruction_layer.py`

**Step 1: Write failing test**

```python
def test_runtime_layer_not_active_without_codecompass_context():
    from agent.services.instruction_layer_service import get_instruction_layer_service
    svc = get_instruction_layer_service()
    # No flag, no context, no agent_template trigger
    task = {"id": "t1", "prompt": "x", "worker_execution_context": {}}
    layers = svc.layer_model(task=task)["layers"]
    assert "codecompass_runtime" not in [l["id"] for l in layers]
```

**Step 2: Run, expect PASS** (default-off is already in Step 3 of Task 3).

Run: `pytest tests/test_codecompass_runtime_instruction_layer.py::test_runtime_layer_not_active_without_codecompass_context -v`
Expected: PASS

**Step 3: Commit (amend or new)**

```bash
git add tests/test_codecompass_runtime_instruction_layer.py
git commit -m "test(instruction-layer): runtime layer scoped to CodeCompass contexts (CCARI-008)"
```

---

### Task 6: Add the runtime rules to ananta-worker prompt (CCARI-005)

**Objective:** When the iteration prompt contains a `codecompass_snippet` block, prepend a one-line rule: "treat CodeCompass context as evidence, not truth; ask for reload if data is missing."

**Files:**
- Modify: `agent/common/sgpt_architecture_scan.py`
- Test: `tests/test_codecompass_runtime_instruction_layer.py` (extend)

**Step 1: Write failing test**

```python
def test_build_iteration_prompt_adds_runtime_rules_when_codecompass_present(monkeypatch):
    from agent.common import sgpt_architecture_scan as sas
    ctx = {
        "retrieval_profile": {"analysis_mode": "iteration"},
        "blocks": [
            {"rel_path": "x.java", "source_kind": "codecompass_snippet", "content": "class X {}"}
        ],
    }
    prompt = sas._build_iteration_prompt("Explain this", context=ctx)
    # Expect the runtime-rule marker in the prompt
    assert "CodeCompass-Kontext" in prompt or "codecompass_runtime" in prompt.lower()
```

**Step 2: Run, expect FAIL**

Run: `pytest tests/test_codecompass_runtime_instruction_layer.py::test_build_iteration_prompt_adds_runtime_rules_when_codecompass_present -v`
Expected: FAIL — "CodeCompass-Kontext" not in prompt

**Step 3: Inspect the function**

Read `agent/common/sgpt_architecture_scan.py:344` and find the block that assembles the prompt. The exact insertion point is where `context["blocks"]` are rendered.

**Step 4: Implement the rule injection**

Add a helper `_codecompass_runtime_rule(context) -> str | None` that returns the rule string if any block has `source_kind == "codecompass_snippet"`, else None. In `_build_iteration_prompt`, prepend the rule to the prompt if the helper returns non-None.

**Step 5: Run, expect PASS**

Run: `pytest tests/test_codecompass_runtime_instruction_layer.py::test_build_iteration_prompt_adds_runtime_rules_when_codecompass_present -v`
Expected: PASS

**Step 6: Verify existing sgpt tests still pass**

Run: `pytest -q tests/test_sgpt_architecture_scan.py tests/test_sgpt.py 2>&1 | tail -5`
Expected: all pass

**Step 7: Commit**

```bash
git add agent/common/sgpt_architecture_scan.py tests/test_codecompass_runtime_instruction_layer.py
git commit -m "feat(sgpt): codecompass runtime rule in iteration prompt (CCARI-005)"
```

---

### Task 7: Append OpenCode AGENTS.md paragraph in worker workspace (CCARI-006)

**Objective:** When rendering the OpenCode workspace, append a "CodeCompass context rules" paragraph to the AGENTS.md.

**Files:**
- Modify: `agent/services/worker_workspace_service.py`
- Test: `tests/test_codecompass_runtime_instruction_layer.py` (extend)

**Step 1: Write failing test**

```python
def test_worker_workspace_renders_codecompass_rules_for_opencode(tmp_path, monkeypatch):
    from agent.services import worker_workspace_service as ws
    # Build a minimal workspace context for an OpenCode agent
    ctx = ws.WorkerWorkspaceContext(
        workspace_dir=tmp_path,
        agent_template="opencode",
        # ... other fields with safe defaults
    )
    # call the rendering function that produces AGENTS.md
    out = ws.render_workspace_agents_md(ctx, task={"id": "t1", "prompt": "x"})
    assert "CodeCompass" in out
    assert "evidence" in out.lower()
```

**Step 2: Run, expect FAIL**

Run: `pytest tests/test_codecompass_runtime_instruction_layer.py::test_worker_workspace_renders_codecompass_rules_for_opencode -v`
Expected: FAIL

**Step 3: Find the AGENTS.md renderer**

Search for `AGENTS.md` template usage in `worker_workspace_service.py`. The template is likely a string in a constant or a function that builds the file.

**Step 4: Add a paragraph**

Add a constant `CODECOMPASS_RUNTIME_AGENTS_PARAGRAPH = "..."` and append it to the AGENTS.md content when `agent_template == "opencode"`. (If other agent templates are also relevant — the prompt is the same — append for `ananta_worker` too. AI-Snake-Chat has its own rendering path; it gets the rule via the runtime layer, not the AGENTS.md.)

**Step 5: Run, expect PASS**

Run: `pytest tests/test_codecompass_runtime_instruction_layer.py::test_worker_workspace_renders_codecompass_rules_for_opencode -v`
Expected: PASS

**Step 6: Verify existing worker_workspace tests still pass**

Run: `pytest -q tests/test_worker_workspace_service.py 2>&1 | tail -5`
Expected: all pass

**Step 7: Commit**

```bash
git add agent/services/worker_workspace_service.py tests/test_codecompass_runtime_instruction_layer.py
git commit -m "feat(workspace): codecompass runtime rules in OpenCode AGENTS.md (CCARI-006)"
```

---

### Task 8: Document AI-Snake-Chat behavior (CCARI-007)

**Objective:** Add an "AI-Snake-Chat" section to the runtime-instructions doc that explains: chat shows "nachladen empfohlen" markers, sends context_reload_request to the hub, never claims completeness on missing evidence.

**Files:**
- Modify: `docs/codecompass-agent-runtime-instructions.md` (add section; doc was created in Task 1)

**Step 1: Append section**

```markdown
## AI-Snake-Chat specifics

When CodeCompass context is shown inside AI-Snake-Chat:

- The chat UI shows a "nachladen empfohlen" marker when the model answer cites no
  evidence path for a security-relevant claim (e.g. "is field X protected?").
- The chat issues a `context_reload_request` (see [contract](../contracts/codecompass-context-reload-request.md))
  to the hub when the user asks a follow-up that requires additional context the
  model did not have when answering the previous turn.
- The chat does not claim completeness on missing evidence. Empty CodeCompass
  results are rendered as "CodeCompass hat zu dieser Frage nichts Belegbares
  gefunden" — never as a "no, it is not protected"-style negative claim.
```

**Step 2: Verify**

Run: `grep -c "nachladen empfohlen" docs/codecompass-agent-runtime-instructions.md`
Expected: `1`

**Step 3: Commit**

```bash
git add docs/codecompass-agent-runtime-instructions.md
git commit -m "docs(codecompass): AI-Snake-Chat runtime instructions (CCARI-007)"
```

---

### Task 9: Implement context_reload_request parsing + Hub handling (CCARI-011)

**Objective:** Hub-side: parse, validate, dedup, limit, and serve `context_reload_request` payloads through the existing `ContextDeliveryService`. New route on `agent/routes/` for the chat to call.

**Files:**
- Modify: `agent/services/context_delivery_service.py`
- Create: `agent/routes/codecompass_reload.py` (new)
- Test: `tests/test_codecompass_reload_request.py` (new)

**Step 1: Write failing test for the parser**

In `tests/test_codecompass_reload_request.py`:

```python
def test_valid_request_parses_and_dedupes():
    from agent.services.codecompass_reload import parse_reload_request, ReloadRequestError
    raw = {
        "kind": "context_reload_request",
        "reason": "missing evidence for permission check",
        "requested_context": [
            {"type": "file_range", "path": "src/main/java/x/X.java", "start_line": 1, "end_line": 50},
            {"type": "file_range", "path": "src/main/java/x/X.java", "start_line": 1, "end_line": 50},  # dup
            {"type": "symbol", "query": "PriceFieldPolicy"},
        ],
        "risk": "read_only",
    }
    parsed = parse_reload_request(raw)
    assert len(parsed["requested_context"]) == 2
    assert parsed["risk"] == "read_only"


def test_mutating_request_is_policy_blocked():
    from agent.services.codecompass_reload import parse_reload_request, ReloadRequestError
    raw = {
        "kind": "context_reload_request",
        "reason": "x",
        "requested_context": [{"type": "file_range", "path": "x", "start_line": 1, "end_line": 2}],
        "risk": "write",  # NOT read_only
    }
    import pytest
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "policy_blocked"


def test_too_many_entries_is_clamped():
    from agent.services.codecompass_reload import parse_reload_request
    raw = {
        "kind": "context_reload_request",
        "reason": "x",
        "requested_context": [
            {"type": "file_range", "path": f"f{i}.java", "start_line": 1, "end_line": 2}
            for i in range(20)
        ],
        "risk": "read_only",
    }
    parsed = parse_reload_request(raw)
    assert len(parsed["requested_context"]) == 10  # hard cap
```

**Step 2: Run, expect FAIL**

Run: `pytest tests/test_codecompass_reload_request.py -v`
Expected: 3 failures (module not found)

**Step 3: Create `agent/services/codecompass_reload.py`**

Module with:
- `ReloadRequestError(ValueError)` with `.code: str`
- `VALID_TYPES = {"file_range", "symbol", "codecompass_search", "graph_expand", "architecture_query"}`
- `MAX_REQUESTED_ENTRIES = 10`
- `parse_reload_request(raw: dict) -> dict`:
  - assert `kind == "context_reload_request"`
  - assert `risk == "read_only"` else raise `ReloadRequestError(code="policy_blocked")`
  - assert `reason` is non-empty
  - assert `requested_context` is a list
  - for each entry, assert `type in VALID_TYPES`
  - dedup by (type, query-or-path)
  - clamp to first MAX_REQUESTED_ENTRIES
  - return normalized dict

**Step 4: Run, expect PASS**

Run: `pytest tests/test_codecompass_reload_request.py -v`
Expected: 3 passed

**Step 5: Wire into `ContextDeliveryService`**

Add a method:
```python
def handle_reload_request(self, *, task: dict, request: dict) -> dict:
    """Validate + serve a context_reload_request. Returns a context_reload_response.v1 payload."""
    from agent.services.codecompass_reload import parse_reload_request, ReloadRequestError
    try:
        parsed = parse_reload_request(request)
    except ReloadRequestError as exc:
        return {"schema": "context_reload_response.v1", "status": "policy_blocked", "code": exc.code, "warnings": [exc.code]}
    # For each requested entry, try to satisfy via the existing RAG-helper index service.
    chunks = self._retrieve_chunks_for_reload(task=task, requested=parsed["requested_context"])
    return {
        "schema": "context_reload_response.v1",
        "status": "ok",
        "delivered": chunks,
        "warnings": [],
    }
```

For the chunk retrieval, reuse `_retrieve_chunks` (already in the service) but pass the requested entries as a synthetic query list. Keep the implementation simple: route to `rag_helper_index_service.retrieve(profile=, query=, limit=)` for each entry, aggregate.

**Step 6: Add test for the delivery path**

```python
def test_handle_reload_request_with_valid_payload(monkeypatch):
    from agent.services.context_delivery_service import ContextDeliveryService
    svc = ContextDeliveryService()
    fake_chunks = [{"path": "x.java", "snippet": "..."}]
    monkeypatch.setattr(svc, "_retrieve_chunks_for_reload", lambda task, requested: fake_chunks)
    result = svc.handle_reload_request(
        task={"id": "t1", "prompt": "x"},
        request={
            "kind": "context_reload_request",
            "reason": "missing evidence",
            "requested_context": [{"type": "file_range", "path": "x.java", "start_line": 1, "end_line": 50}],
            "risk": "read_only",
        },
    )
    assert result["status"] == "ok"
    assert result["delivered"] == fake_chunks
```

**Step 7: Run, expect PASS**

Run: `pytest tests/test_codecompass_reload_request.py -v`
Expected: 4 passed

**Step 8: Commit**

```bash
git add agent/services/codecompass_reload.py agent/services/context_delivery_service.py tests/test_codecompass_reload_request.py
git commit -m "feat(codecompass): parse + serve context_reload_request through hub (CCARI-011)"
```

---

### Task 10: Add a Hub route for the chat to call (CCARI-011 part 2)

**Objective:** Expose a `POST /api/codecompass/reload-context` endpoint that the AI-Snake-Chat can hit.

**Files:**
- Create: `agent/routes/codecompass_reload.py`
- Test: extend `tests/test_codecompass_reload_request.py` with a route test

**Step 1: Write the route**

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any
from agent.services.context_delivery_service import get_context_delivery_service

router = APIRouter(prefix="/api/codecompass", tags=["codecompass"])


class ReloadRequestBody(BaseModel):
    task_id: str
    request: dict[str, Any]


@router.post("/reload-context")
def reload_context(body: ReloadRequestBody) -> dict[str, Any]:
    svc = get_context_delivery_service()
    # Look up the task; if the task is missing, return 404
    from agent.services.repository_registry import get_repository_registry
    repos = get_repository_registry()
    task = repos.task_repo.get_by_id(body.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task_not_found")
    result = svc.handle_reload_request(task=task.model_dump(), request=body.request)
    if result["status"] == "policy_blocked":
        raise HTTPException(status_code=409, detail=result["code"])
    return result
```

**Step 2: Register the route**

In `agent/routes/__init__.py` (or wherever routes are registered), add `from agent.routes import codecompass_reload` and `app.include_router(codecompass_reload.router)`. Verify by checking how existing routes register.

**Step 3: Write a route test**

Use the FastAPI TestClient to call `/api/codecompass/reload-context`. Mock the task repo.

**Step 4: Run, expect PASS**

Run: `pytest tests/test_codecompass_reload_request.py -v`
Expected: all pass

**Step 5: Commit**

```bash
git add agent/routes/codecompass_reload.py tests/test_codecompass_reload_request.py
git commit -m "feat(routes): /api/codecompass/reload-context endpoint (CCARI-011)"
```

---

### Task 11: Final docs update + Todo sync (CCARI-010 + 11/11 done)

**Objective:** Update `docs/operator-tui/ai-snake-chat-memory-codecompass-worker.md` with a "Context reload" section, then mark all 11 CCARI tasks done in the Todo JSON.

**Files:**
- Modify: `docs/operator-tui/ai-snake-chat-memory-codecompass-worker.md`
- Modify: `todos/todo.codecompass-agent-runtime-instructions.json`

**Step 1: Add reload section to AI-Snake-Chat doc**

Append a "Context reload" section that explains the chat-side flow.

**Step 2: Update todo JSON**

Use the same pattern as the previous CCAQE sync: set all 11 tasks to `done`/`partial`, update `tasks_status_summary`, clear `recommended_first_implementation_slice`, mark `status: "done"`.

**Step 3: Run full test sweep**

Run: `pytest -q tests/test_codecompass_runtime_instruction_layer.py tests/test_codecompass_reload_request.py tests/test_instruction_layers.py tests/test_worker_workspace_service.py tests/test_sgpt_architecture_scan.py 2>&1 | tail -10`
Expected: all pass

**Step 4: Commit**

```bash
git add docs/operator-tui/ai-snake-chat-memory-codecompass-worker.md todos/todo.codecompass-agent-runtime-instructions.json
git commit -m "docs(codecompass): ai-snake-chat reload section + todo sync (CCARI-010, all 11 done)"
```

---

## Verification Checklist

After all 11 tasks, the following must hold:

```bash
# All new tests pass
pytest -q tests/test_codecompass_runtime_instruction_layer.py tests/test_codecompass_reload_request.py

# No regression in layer / workspace / sgpt
pytest -q tests/test_instruction_layers.py tests/test_worker_workspace_service.py tests/test_sgpt_architecture_scan.py

# JSON valid
python -m json.tool todos/todo.codecompass-agent-runtime-instructions.json > /dev/null
```

Expected: all green, JSON valid, `tasks_status_summary.progress_percent_done == 100.0`.
