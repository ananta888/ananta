# Run-Control Layer

Ananta führt lange Arbeiten weitgehend automatisch durch. Der Run-Control-Layer macht diese Arbeit steuerbar, ohne ständig nachzufragen — durch sichtbare Zwischenergebnisse, sichere Eingriffspunkte und menschliche Genehmigungen nur dort, wo sie tatsächlich nötig sind.

## Konzept

```
Automatische Arbeit → [Checkpoint] → Eingriff möglich → Weiterarbeit
                                ↓
                   Approval Gate  /  Instruction-Injection  /  Branch-Auswahl
```

**Hub ist Owner**: Alle Run-Control-Mutationen gehen über Hub-APIs. Angular und TUI sind Client Surfaces. Worker erhalten keine direkten Befehle vom UI.

**Eingriffe werden als Events modelliert**: Nicht als unsichtbare Prompt-Manipulation, sondern als `RunCommand`-Objekte mit actor, timestamp, status und Audit-Referenz.

**Safe-Point-Semantik**: Instruction-Injection unterbricht laufende Worker nicht sofort. Die Anweisung wird gespeichert und an der nächsten sicheren Iterationsgrenze angewendet.

---

## Domain-Modell

### RunCommand

| Feld | Beschreibung |
|---|---|
| `command_id` | UUID |
| `type` | pause_run / resume_run / cancel_run / retry_run_or_task / inject_instruction / select_branch / approve_gate / deny_gate |
| `task_id` / `goal_id` | Scope |
| `payload` | Typ-spezifische Parameter |
| `requested_by` | Actor (Username) |
| `status` | accepted / rejected_by_policy / applied / failed / superseded |
| `idempotency_key` | Verhindert doppelte Ausführung |

### RunStatus (aggregiert aus Task-Status + Approvals + Branches)

| Wert | Bedeutung |
|---|---|
| `running` | Task läuft (in_progress / assigned / delegated / proposing) |
| `planning` | Task in Planung (todo / created) |
| `paused` | Task manuell pausiert |
| `waiting_for_approval` | Mind. ein Approval Gate offen |
| `waiting_for_branch_selection` | Branch-Kandidaten vorhanden, keiner ausgewählt |
| `applying_intervention` | Aktive Operator-Instruction wartet auf Anwendung |
| `cancelled` / `completed` / `failed` | Terminal-Zustände |

### OperatorInstruction

Wird via `inject_instruction` Command erstellt. Modus:
- `next_iteration_instruction` — an nächster Iterationsgrenze anwenden (Standard)
- `pause_then_apply` — Task zuerst pausieren, dann anwenden
- `context_note_only` — nur als Kontext, überschreibt keine bestehenden Anweisungen

### BranchCandidate

Varianten für Multi-LLM-Vergleich, Planner-Alternativen, Reparaturstrategien. Status: `proposed → selected` (Alternativen → `paused`).

---

## API

### RunCommand senden

```bash
# Pause via neuer Command-API
curl -X POST /api/tasks/{task_id}/commands \
  -H 'Content-Type: application/json' \
  -d '{"type": "pause_run", "idempotency_key": "op-123:t-456:pause:1"}'

# Resume mit Instruction
curl -X POST /api/tasks/{task_id}/commands \
  -d '{"type": "resume_run", "payload": {"instruction": "Keine React-Lösung", "mode": "next_iteration_instruction"}}'

# Instruction injizieren (ohne Status-Änderung)
curl -X POST /api/tasks/{task_id}/commands \
  -d '{"type": "inject_instruction", "payload": {"text": "Keine React-Lösung", "mode": "next_iteration_instruction", "instruction_class": "constraint"}}'

# Approval Gate genehmigen
curl -X POST /api/tasks/{task_id}/commands \
  -d '{"type": "approve_gate", "payload": {"approval_id": "{approval_id}", "reason": "Operator reviewed scope."}}'

# Approval Gate ablehnen
curl -X POST /api/tasks/{task_id}/commands \
  -d '{"type": "deny_gate", "payload": {"approval_id": "{approval_id}", "reason": "Scope zu groß."}}'

# Branch auswählen
curl -X POST /api/tasks/{task_id}/commands \
  -d '{"type": "select_branch", "payload": {"branch_id": "{branch_id}", "reason": "Sicherere Variante bevorzugt."}}'
```

### Control-State lesen

```bash
# Task Control-State
GET /api/tasks/{task_id}/control-state

# Dashboard: alle aktiven Tasks
GET /api/runs/active-control-state?limit=50

# Goal-State
GET /api/goals/{goal_id}/control-state
```

### Bestehende Endpunkte (bleiben erhalten, backward-compatible)

```bash
POST /tasks/{task_id}/pause       # → intern: pause_run Command
POST /tasks/{task_id}/resume
POST /tasks/{task_id}/cancel
POST /tasks/{task_id}/retry
POST /api/approvals/{id}/decision # → Approval-Lifecycle (direkt, ohne RunCommand)
```

### Branches

```bash
GET  /api/tasks/{task_id}/branches          # Liste der Branches
POST /api/tasks/{task_id}/branches          # Branch anlegen
```

---

## Abgrenzung

**Run-Control** vs. **deterministischer Single-Step-Run** (`/api/deterministic/run`):
- `/api/deterministic/run` führt einen einzelnen deterministischen Schritt aus (Shell/API/Regex/File/Python)
- Run-Control steuert langlebige Task-/Goal-Läufe mit Worker-Dispatching und Approval-Lifecycle

**Instruction-Injection** vs. **direkte Prompt-Manipulation**:
- Injizierte Instructions sind als `operator_instruction` markiert, auditierbar, priorisiert unter Policies
- Sie überschreiben keine Approval-Gates, Tool-Policies oder Workspace-Scope-Policies

---

## Angular UI

Im **Task-Detail** gibt es einen Tab **Steuerung** mit:
- Pause / Resume / Cancel / Retry — Button-Verfügbarkeit aus Control-State
- Instruction-Injection mit Modus- und Klassen-Auswahl
- Pending Approval Gates mit Grant/Deny und Scope-Summary
- Branch/Variant-Auswahl
- Command-Timeline der letzten Eingriffe

Im **Board** zeigen Karten Badges für `⏸ Pausiert`, `🔐 Approval` etc. — direkt klickbar in Task-Detail Steuerung-Tab.

---

## Operator-TUI

```
:run status <task_id>                        Status und offene Eingriffe anzeigen
:run pause <task_id>                         Task pausieren
:run resume <task_id> [--instruction TEXT]   Task fortsetzen, optional mit Anweisung
:run cancel <task_id> --yes                  Task abbrechen (Bestätigung nötig)
:run retry <task_id>                         Task wiederholen
:run inject <task_id> <text>                 Anweisung injizieren
:run inject <task_id> --mode pause_then_apply <text>

:approval list [--status pending]            Pending Approvals auflisten
:approval grant <id> --task <task_id>        Gate genehmigen
:approval deny <id> --task <task_id> --reason TEXT
:approval status <id>                        Status eines Approval-Requests

:branch list <task_id>                       Branches auflisten
:branch select <task_id> <branch_id>         Branch auswählen
```

TUI-Kommandos dispatchen ausschließlich an Hub-API. Riskante Aktionen (cancel) zeigen eine Bestätigungsaufforderung.

---

## Security

- **Hub-owned**: Keine direkte Worker-Mutation von UI oder TUI
- **Audit-Events**: Jede Mutation erzeugt `run_command_applied`/`run_command_rejected` Events
- **Idempotency**: Gleicher `idempotency_key` → kein doppelter Statuswechsel
- **Instruction-Payload-Limits**: max. 4000 Zeichen, kein Binary, keine Attachment-Tricks
- **Approval-Delegation**: Nur über `ApprovalRequestService.decide_request()` — kein zweiter Lifecycle
- **Audit-Logs**: Keine raw prompts, Secrets oder große Content-Payloads
- **Branch-Auswahl**: Persistiert im Backend — nicht nur UI-seitig

## Human-in-the-Loop Gates

Gates sind nicht "Nachfragen bei jeder Aktion", sondern gezielte Stopps für:
- Externe, irreversible Aktionen (Dateien löschen, Deploys)
- Finanzielle oder security-relevante Side Effects
- Konfigurierte `human_required_tools`

Harmlose Annahmen und interne Schritte laufen weiter ohne Gate. Das Ziel ist minimale Unterbrechung bei maximaler Transparenz.
