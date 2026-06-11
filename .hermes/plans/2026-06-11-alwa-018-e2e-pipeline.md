# Plan: ALWA-018 — End-to-End Approval-Lifecycle Pipeline Tests

## Goal
Coverage-Lücke schließen für die volle Approval-Pipeline (write → approval → decision → re-dispatch). ALWA-018 wird von "PARTIAL" auf "done" gezogen, ohne neue Production-Logik — die Pipeline ist seit Welle 2 vorhanden, hier wird sie als E2E verifiziert.

## Scope

IN SCOPE:
- 3 Pipeline-E2E-Tests in `tests/test_alwa_e2e_pipeline.py` (NEU)
- 1 Test-Helper für in-memory DB + Task-Repo
- Mark ALWA-018 als done

OUT OF SCOPE:
- Neue Production-Logik (Pipeline ist seit 33d820 + M4 Welle 1/2 implementiert)
- HTTP-Layer (Flask-Routen sind in ALWA-009 separat getestet)
- Goal-Pre-Approval-E2E (in test_approval_binding.py schon indirekt gedeckt)

## Test-Szenarien

### Test 1: `test_pipeline_grant_redispatches_and_unblocks`
Worker ruft `repo.write_file` auf:
1. `task.status = "pending_approval"` (vom Tool-Loop gesetzt)
2. `ApprovalRequestService.create_pending_request(...)` → status=pending
3. Operator ruft `decide_request(request.id, decision="granted", decided_by="operator")`
4. Erwartet:
   - request.status = "granted"
   - audit `approval_request_decided` + `approval_request_redispatch`
   - task.status = "todo", status_reason_code = "approval_granted_redispatch"
5. Re-Execute: `resolve_grant_for_call(tool_name, args, task_id, goal_id)` → grant zurück
6. `consume_request(request.id)` → status=consumed
7. Erwartet: audit `approval_request_consumed`

### Test 2: `test_pipeline_deny_leaves_task_blocked`
Worker ruft `repo.write_file` auf, operator denies:
1. create → pending
2. decide(decision="denied", decided_by="operator")
3. Erwartet:
   - request.status = "denied"
   - task.status NICHT geändert (bleibt pending_approval)
   - resolve_grant_for_call → None
4. Negative Pfad: zweiter Decide-Versuch → `ApprovalDecisionError("request_already_denied", 409)`

### Test 3: `test_pipeline_digest_mismatch_blocks_reuse`
Grant wird erstellt für call A (path="a.py", content="X"). Operator grants.
Re-Execute versucht call B (path="a.py", content="Y"):
1. create für A → granted
2. resolve_grant_for_call(call_A) → grant (consume noch nicht)
3. resolve_grant_for_call(call_B) → None (digest mismatch)
4. audit prüft: KEIN `approval_request_consumed` für B
5. consume(A) → success, status=consumed
6. resolve_grant_for_call(call_A) nochmal → None (consumed filter)

## Implementation

### File 1 (NEU): `tests/test_alwa_e2e_pipeline.py`
- 3 Tests + 1 helper `_build_in_memory_world(monkeypatch)` der:
  - `agent.database.engine` auf in-memory sqlite patcht
  - `SQLModel.metadata.create_all(engine)` aufruft
  - `agent.services.repository_registry.get_repository_registry` mit Fake-Registry ersetzt
  - `agent.common.audit.log_audit` captured
  - DB nach jedem Test via monkeypatch.undo zurückgesetzt (kein Cross-Test-Leak)

### File 2 (KEINE): Production unangetastet

## Verification
- `python -m pytest tests/test_alwa_e2e_pipeline.py -v` → 3 passed
- `python -m pytest tests/test_alwa_*.py tests/test_approval_*.py` → alle bestehenden grün
- `python -m pytest tests/` → 71+3 = 74 passed minimum

## Commit
- `test(alwa): E2E pipeline coverage (ALWA-018)` — neue Datei + Todo-Sync

## Risk
- Niedrig: nur Test-Code, keine Source-Änderungen
- In-memory engine könnte Schema-Mismatch mit app-engine haben → Test setzt create_all auf metadata
