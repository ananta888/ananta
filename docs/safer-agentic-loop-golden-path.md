# Safer Agentic Loop Golden Path

Dieses Dokument beschreibt den Standardpfad fuer den sicheren Control-Layer-Loop aus Routing, Approval, Context und Loop-Guard.

## Golden Path

1. **Task-Propose**
   - Task-Kind wird normalisiert.
   - Context-Bundle wird ueber den gemeinsamen Context-Manager aufgeloest/erstellt.
   - Tool-Router waehlt Backend anhand Capabilities + Governance.

2. **Approval-Gate**
   - Unified Approval klassifiziert den geplanten Schritt (`allow|confirm_required|blocked`).
   - Bei spezialisierten Backends (z. B. `ml_intern`) kann `confirm_required` erzwungen werden.

3. **Execution**
   - Ausfuehrung laeuft task-scoped mit Policy/Guardrails.
   - Loop-Signale und Approval-Entscheidungen werden in die Task-History geschrieben.

4. **Context-Compaction**
   - Bei Budgetdruck greift `priority-budget-compaction-v1`.
   - Drops sind nachvollziehbar (`dropped_reasons`, `dropped_chunks`) und behalten Provenance.

5. **Read-Model / Observability**
   - `/tasks/orchestration/read-model` liefert:
     - `worker_execution_reconciliation`
     - `artifact_flow`
     - `control_layer_observability` (Loop/Routing/Approval/Context)

## Contract-Erwartungen

- Kein stilles Verschlucken von Governance- oder Budget-Entscheidungen.
- Fallbacks muessen als `alternatives[]` inkl. `reason` sichtbar sein.
- Approval-Status bleibt fuer UI und Operatoren maschinenlesbar.
