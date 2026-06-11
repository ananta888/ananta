# ALWA M4 Welle 1 — Audit-Konstanten-Migration + MutationGate Tests

> **Scope:** Welle 1 von M4 (Audit-Kette). Bereitet Welle 2 (Hook-Integration
> in `refresh_mutation_baseline` und `_hub_check`) vor. Löst **nicht**
> ALWA-013/014/015 in einem Rutsch — das wird Welle 2, mit den neuen
> Helpern als sichere Grundlage.

**Goal:** Migration `ananta_worker_mutation_*` → `workspace_*`-Konstanten
abschließen (Supersede) plus einen `audit_workspace_mutation_event()`
Helper bereitstellen. MutationGate digest-Anbindung durch Tests verifizieren.

**Architecture:** Additiv. Alte Konstanten werden NICHT gelöscht, sondern
als deprecated alias gehalten, der den neuen Konstanten-Wert emittiert.
So bricht kein bestehender Konsument (z.B. ein Dashboard, das auf den
alten String filtert). Ein gemeinsamer Helper normalisiert Redaction +
changed_paths-Truncation einmalig. Tests sind RED-GREEN.

**Tech Stack:** Python 3.11+, sqlmodel, pytest, agent/common/audit.py
bestehende Infrastruktur.

---

## Source-First-Discovery (verbindlich vor Implementierung)

Reihenfolge der Sweep-Treffer, die diese Welle berührt:

  • agent/common/audit.py:14-20
      AUDIT_WORKER_TOOL_REQUESTED...AUDIT_WORKER_MUTATION_BLOCKED
      _FORBIDDEN_RAW_FIELDS, _sanitize_details, audit_worker_tool_event
  • agent/common/sgpt_workspace_mutation.py:273-286
      _hub_check emittiert AUDIT_WORKER_MUTATION_EVALUATED mit
      `audit_worker_tool_event` (nicht das neue Event).
  • agent/services/mutation_gate_service.py:415-465
      _compute_call_digest, _resolve_request_grant, _audit_legacy_bypass
  • tests/test_mutation_gate_service.py:247 Zeilen, 10+ Tests,
      KEIN bestehender Test für digest-Mismatch, KEIN Test für
      `_resolve_request_grant`, KEIN Test für `arguments_digest`-Pfad
      in `_validate_scoped_approval`.

**Verdict:** ALWA-006 ist zu ~70% implementiert; Lücken sind ausschließlich
Tests. ALWA-012 ist eine reine additive Konstanten-Migration mit
Helper-Funktion, kein Verhaltens-Bruch.

---

## Tasks (jeder = 2-5 min focused work)

### Task 1: RED-Test für neuen Helper `audit_workspace_mutation_event`

**Files:**
- Create/Modify: `tests/test_alwa_workspace_audit_helper.py`

**Schritt 1 — Test schreiben (RED):**

```python
from agent.common.audit import (
    AUDIT_WORKSPACE_BASELINE_CREATED,
    AUDIT_WORKSPACE_MUTATION_EVALUATED,
    AUDIT_WORKSPACE_MUTATION_BLOCKED,
    audit_workspace_mutation_event,
)


def test_workspace_audit_constants_exist() -> None:
    assert AUDIT_WORKSPACE_BASELINE_CREATED == "workspace_baseline_created"
    assert AUDIT_WORKSPACE_MUTATION_EVALUATED == "workspace_mutation_evaluated"
    assert AUDIT_WORKSPACE_MUTATION_BLOCKED == "workspace_mutation_blocked"


def test_helper_redacts_prompt_and_full_diff(monkeypatch) -> None:
    captured = {}
    def fake_log(action, details):
        captured["action"] = action
        captured["details"] = details
    monkeypatch.setattr("agent.common.audit.log_audit", fake_log)
    audit_workspace_mutation_event(
        AUDIT_WORKSPACE_MUTATION_BLOCKED,
        task_id="t1", goal_id="g1", trace_id="tr1",
        iteration_number=3, mutation_mode="controlled_workspace",
        changed_paths=["a.py", "b.py"],
        diff_hash="abc123",
        policy_decision="violation",
        violation_ids=["V001"], violation_summary="outside manifest",
        blocked_reason="forbidden_path",
        # forbidden fields below MUST be dropped
        prompt="leak me", raw_messages=["x"], full_diff="--- huge diff ---",
    )
    assert captured["action"] == "workspace_mutation_blocked"
    details = captured["details"]
    assert "prompt" not in details
    assert "raw_messages" not in details
    assert "full_diff" not in details
    assert details["changed_paths"] == ["a.py", "b.py"]
    assert details["diff_hash"] == "abc123"


def test_helper_truncates_changed_paths(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        "agent.common.audit.log_audit",
        lambda a, d: captured.update(d=d, a=a),
    )
    audit_workspace_mutation_event(
        AUDIT_WORKSPACE_MUTATION_EVALUATED,
        task_id="t", changed_paths=[f"path_{i}.py" for i in range(200)],
        diff_hash="h", policy_decision="allowed",
    )
    assert captured["a"] == "workspace_mutation_evaluated"
    assert len(captured["d"]["changed_paths"]) <= 50
    assert captured["d"]["changed_paths_truncated"] is True
    assert captured["d"]["changed_paths_count"] == 200
```

**Schritt 2 — Test laufen lassen, RED bestätigen:**

```bash
python -m pytest tests/test_alwa_workspace_audit_helper.py -v
```

Erwartung: FAIL mit `ImportError: cannot import name 'audit_workspace_mutation_event'`.

**Schritt 3 — Commit (RED):**

```bash
rm -f .git/index.lock
git add tests/test_alwa_workspace_audit_helper.py
git commit -m "test(audit): add failing tests for workspace_audit helper (ALWA-012)"
```

---

### Task 2: GREEN — Konstanten + Helper implementieren

**Files:**
- Modify: `agent/common/audit.py:14-29`

**Schritt 1 — Konstanten hinzufügen (additive Migration):**

```python
# ALWA-012: workspace-audit event constants (canonical names).
# The legacy ananta_worker_mutation_* names are kept as deprecated
# aliases that emit the new event value (no two-name split per task).
AUDIT_WORKSPACE_BASELINE_CREATED = "workspace_baseline_created"
AUDIT_WORKSPACE_MUTATION_EVALUATED = "workspace_mutation_evaluated"
AUDIT_WORKSPACE_MUTATION_BLOCKED = "workspace_mutation_blocked"

# Deprecated aliases — kept for back-compat with dashboards / log
# queries that still filter on the old event names. They MUST emit
# the canonical value (no double-event-naming).
AUDIT_WORKER_MUTATION_EVALUATED = AUDIT_WORKSPACE_MUTATION_EVALUATED
AUDIT_WORKER_MUTATION_BLOCKED = AUDIT_WORKSPACE_MUTATION_BLOCKED
```

**Schritt 2 — Helper `audit_workspace_mutation_event` hinzufügen** (nach
`audit_worker_tool_event`, vor `log_audit`):

```python
_WORKSPACE_AUDIT_PATH_LIMIT = 50
_WORKSPACE_AUDIT_FORBIDDEN = _FORBIDDEN_RAW_FIELDS | {
    "full_diff", "unified_diff", "file_content", "raw_content",
    "before", "after",
}


def _truncate_changed_paths(paths: list[str] | None) -> tuple[list[str], bool, int]:
    if not paths:
        return [], False, 0
    sorted_paths = sorted({str(p) for p in paths if str(p).strip()})
    total = len(sorted_paths)
    if total <= _WORKSPACE_AUDIT_PATH_LIMIT:
        return sorted_paths, False, total
    return sorted_paths[:_WORKSPACE_AUDIT_PATH_LIMIT], True, total


def audit_workspace_mutation_event(
    action: str,
    *,
    task_id: str | None = None,
    goal_id: str | None = None,
    trace_id: str | None = None,
    iteration_number: int | None = None,
    mutation_mode: str | None = None,
    changed_paths: list[str] | None = None,
    diff_hash: str | None = None,
    diff_artifact_id: str | None = None,
    policy_decision: str | None = None,
    violation_ids: list[str] | None = None,
    violation_summary: str | None = None,
    blocked_reason: str | None = None,
    tests_result_ref: str | None = None,
    baseline_id: str | None = None,
    baseline_hash: str | None = None,
    workspace_root_hash_or_id: str | None = None,
    materialized_paths_count: int | None = None,
    **extras: Any,
) -> None:
    """ALWA-012: emit one workspace-audit event with ALWA-DD-006 redaction.

    Forbidden raw fields (prompt, raw_messages, full_diff, file_content,
    ...) are dropped before the audit row is written. changed_paths are
    sorted and truncated with a count + truncated flag. Content stays
    out: only paths, hashes, IDs and short summaries reach the log.
    """
    paths, truncated, total = _truncate_changed_paths(changed_paths)
    details: dict[str, Any] = {
        "task_id": task_id,
        "goal_id": goal_id,
        "trace_id": trace_id,
        "iteration_number": iteration_number,
        "mutation_mode": mutation_mode,
        "changed_paths": paths,
        "changed_paths_count": total,
        "changed_paths_truncated": truncated,
        "diff_hash": diff_hash,
        "diff_artifact_id": diff_artifact_id,
        "policy_decision": policy_decision,
        "violation_ids": list(violation_ids or []),
        "violation_summary": violation_summary,
        "blocked_reason": blocked_reason,
        "tests_result_ref": tests_result_ref,
        "baseline_id": baseline_id,
        "baseline_hash": baseline_hash,
        "workspace_root_hash_or_id": workspace_root_hash_or_id,
        "materialized_paths_count": materialized_paths_count,
    }
    # Filter None values to keep the audit row compact.
    details = {k: v for k, v in details.items() if v is not None}
    details = {**details, **extras}
    # Drop forbidden raw fields explicitly so callers can never smuggle
    # them in via **extras.
    details = {k: v for k, v in details.items() if str(k).lower() not in _WORKSPACE_AUDIT_FORBIDDEN}
    log_audit(action, details)
```

**Schritt 3 — Tests grün laufen lassen:**

```bash
python -m pytest tests/test_alwa_workspace_audit_helper.py -v
python -m pytest tests/test_audit_sanitization.py tests/test_artifact_first_audit.py -v
```

Erwartung: GREEN. Bestehende Audit-Tests dürfen NICHT brechen (Alias-Wert
bleibt String-kompatibel).

**Schritt 4 — Commit (GREEN):**

```bash
rm -f .git/index.lock
git add agent/common/audit.py
git commit -m "feat(audit): add workspace_audit helper + canonical event constants (ALWA-012)"
```

---

### Task 3: RED-Test — MutationGate arguments_digest Mismatch

**Files:**
- Modify: `tests/test_mutation_gate_service.py` (ans Ende)

**Schritt 1 — Test schreiben:**

```python
def test_mutation_scope_arguments_digest_mismatch_blocks() -> None:
    svc = get_mutation_gate_service()
    task = {
        "id": "task-d1",
        "goal_id": "goal-d1",
        "mutation_approval": {
            "task_id": "task-d1",
            "trace_id": "trace-d1",
            "actor": "operator",
            "mutation_classes": ["shell"],
            "expires_at": time.time() + 600,
            "target_fingerprint": "fp-expected",
            "arguments_digest": "deadbeef" * 8,  # 64 hex
        },
    }
    target = svc.normalize_target(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task=task,
    )
    result = svc._validate_scoped_approval(
        task=task,
        mutation_class="shell",
        normalized_target=target,
        trace_id="trace-d1",
        actor="operator",
        arguments_digest="cafebabe" * 8,  # different digest
    )
    assert result["ok"] is False
    assert result["reason_code"] == "mutation_scope_mismatch:arguments_digest"


def test_mutation_scope_arguments_digest_match_allows() -> None:
    svc = get_mutation_gate_service()
    target = svc.normalize_target(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task={"id": "task-d2", "goal_id": "goal-d2"},
    )
    arguments_digest = svc._compute_call_digest(
        call_arguments={"command": "chmod +x scripts/run.sh"},
        target_fingerprint=target["target_fingerprint"],
    )
    task = {
        "id": "task-d2",
        "goal_id": "goal-d2",
        "mutation_approval": {
            "task_id": "task-d2",
            "trace_id": "trace-d2",
            "actor": "operator",
            "mutation_classes": ["shell"],
            "expires_at": time.time() + 600,
            "target_fingerprint": target["target_fingerprint"],
            "arguments_digest": arguments_digest,
        },
    }
    result = svc._validate_scoped_approval(
        task=task,
        mutation_class="shell",
        normalized_target=target,
        trace_id="trace-d2",
        actor="operator",
        arguments_digest=arguments_digest,
    )
    assert result["ok"] is True
    assert result["reason_code"] == "mutation_scope_ok"
```

**Schritt 2 — Tests laufen lassen — müssen bereits GREEN sein**, weil
die Implementierung in 33d820 schon existiert. Falls RED → Bug in der
Discovery, dann GREEN-Task mit Fix anhängen.

```bash
python -m pytest tests/test_mutation_gate_service.py -v
```

**Schritt 3 — Commit (Tests):**

```bash
rm -f .git/index.lock
git add tests/test_mutation_gate_service.py
git commit -m "test(mutation-gate): cover arguments_digest match/mismatch (ALWA-006)"
```

---

### Task 4: ALWA-Task-Status im Track aktualisieren

**Files:**
- Modify: `todos/todo.approval-lifecycle-workspace-audit.json`

**Schritt 1 — Tasks 006 + 012 markieren:**

In jedem Task-Objekt:
- `status: "done"` (mit evidence-block — siehe Source-First-Skill)
- `"evidence": "agent/services/mutation_gate_service.py:415-465 + tests/test_mutation_gate_service.py:248+"`
- `"result": "audit helper green, mutation-gate digest tests green"`

**Schritt 2 — Tasks 013/014/015 NICHT als done markieren** (sind Welle 2).

**Schritt 3 — `tasks_status_summary` Block NUR regenerieren, nicht von
Hand** (siehe `references/todo-status-sync-pattern.md` im
ananta-subsystem-discovery-Skill). Wenn `scripts/todo_status_sync.py`
nicht existiert, hand-edit mit den korrekten Zählerwerten:

  total: 20, done: 8 (war 6), in_progress: 0, todo: 12

**Schritt 4 — Commit:**

```bash
rm -f .git/index.lock
git add todos/todo.approval-lifecycle-workspace-audit.json
git commit -m "docs(todos): mark ALWA-006 + ALWA-012 done after Welle 1 (audit + tests)"
```

---

## Definition of Done (für Welle 1)

  • `AUDIT_WORKSPACE_*` Konstanten exportiert, ALIAS-Werte stringgleich
    mit den neuen Werten (kein doppelter Event-Name).
  • `audit_workspace_mutation_event()` redigiert verbotene Felder,
    trunkiert changed_paths, akzeptiert nur Pfade/Hashes/IDs.
  • `tests/test_alwa_workspace_audit_helper.py`: 3 Tests grün.
  • `tests/test_mutation_gate_service.py`: 2 neue digest-Tests grün,
    10+ alte Tests weiterhin grün (kein Verhaltens-Bruch).
  • Bestehende `test_audit_sanitization.py` + `test_artifact_first_audit.py`
    + `test_audit_event_schema.py` weiterhin grün.
  • Track-Status: 8/20 done, klar markiert welche offen sind.
  • Kein `git add .` — jedes File namentlich.

## Out of Scope (für Welle 2)

  • ALWA-013: Hook in `refresh_mutation_baseline`
  • ALWA-014: Hook in `_hub_check` final
  • ALWA-015: Hook in `_hub_check` + mutation_gate blocked
  • Migration `AUDIT_WORKER_MUTATION_*` Aufrufstellen in
    `sgpt_workspace_mutation.py:273-286` — passiert in Welle 2,
    wenn die Hooks die Helper aufrufen.
  • Doc-Sync (ALWA-016/017/019) — eigene Welle.
