# Agent Run State Machine
<!-- COSMOS-002 -->

## Zweck

Jeder Agentenlauf (Run) hat einen expliziten Lebenszyklus. Zustandsübergänge sind
definiert, auditierbar und persistent. Kein implizites "läuft irgendwie weiter".

---

## Zustände

```
created
  │
  ▼
queued
  │
  ▼
planning ──────────────────────────────────────────────────► failed
  │                                                              ▲
  ▼                                                              │
waiting_for_context ──────────────────────────────────────────► │
  │                                                              │
  ▼                                                              │
waiting_for_approval ─────────────────────────────────────────► │
  │                  ◄── approval denied ────────────────────── │
  ▼                                                              │
running ───────────────────────────────────────────────────────► │
  │                                                              │
  ▼                                                              │
verifying ─────────────────────────────────────────────────────► │
  │
  ├──► completed
  │
  └──► failed

Aus jedem Zustand außer completed/failed/cancelled: ──► cancelled
```

---

## Übergangstabelle

| Von                  | Nach                  | Auslöser                                               |
|----------------------|-----------------------|--------------------------------------------------------|
| created              | queued                | Hub nimmt Run an, Policy-Check bestanden               |
| queued               | planning              | Worker wird zugeteilt, Expert geladen                  |
| planning             | waiting_for_context   | Plan benötigt Kontext-Bundle                           |
| planning             | waiting_for_approval  | Plan enthält approval-pflichtige Schritte              |
| planning             | running               | Plan vollständig, keine externen Abhängigkeiten        |
| waiting_for_context  | planning              | Kontext-Bundle geliefert                               |
| waiting_for_context  | failed                | Kontext-Timeout oder Policy-Ablehnung                  |
| waiting_for_approval | running               | Approval erteilt                                       |
| waiting_for_approval | failed                | Approval abgelehnt oder abgelaufen                     |
| running              | verifying             | Worker-Output vorhanden                                |
| running              | waiting_for_approval  | Laufzeit-Gate aufgetaucht (z.B. apply_diff)            |
| running              | failed                | Worker-Fehler, Timeout, Policy-Verletzung              |
| verifying            | completed             | Verifikation bestanden (Tests grün, Gates erfüllt)     |
| verifying            | failed                | Verifikation fehlgeschlagen                            |
| *                    | cancelled             | cancel() durch Operator oder Hub-Timeout               |

Ungültige Übergänge werden hart abgelehnt und als Audit-Event gespeichert.

---

## Run-Felder

```python
@dataclass
class AgentRun:
    run_id: str           # uuid, unveränderlich
    goal_id: str          # referenziertes Goal
    correlation_id: str   # für verteilte Traces / verkettete Runs
    expert_id: str        # aktiver Expert zur Laufzeit
    expert_version: str   # gesperrte Version, nicht "latest"
    policy_scope_id: str  # aktive Policy-Scope

    state: RunState       # Enum: created | queued | ... | completed
    created_at: float     # unix timestamp
    updated_at: float
    started_at: float | None
    completed_at: float | None

    error_reason: str | None      # menschenlesbar
    failed_at_step: str | None    # Schritt-ID oder Beschreibung
    error_code: str | None        # maschinenlesbar: "timeout" | "policy_denied" | ...
    recovery_options: list[str]   # "retry" | "skip" | "abort" | "escalate_to_human"

    artifacts: list[str]          # artifact_id-Liste (nicht Inline-Content)
    metadata: dict                # erweiterbar, nicht für sensitive Felder
```

---

## Fehlerbehandlung

Jeder Fehler speichert:
- `failed_at_step`: wo genau (Planung, Kontext-Abfrage, Tool-Call-Schritt, Verifikation)
- `error_code`: strukturierter Fehlertyp (Maschinen-lesbar für UI und Retry-Logik)
- `recovery_options`: was der Operator als nächstes tun kann

Recovery-Optionen:
- `retry`: Schritt idempotent wiederholbar, kein menschlicher Eingriff nötig
- `skip`: Schritt überspringen und weitermachen (nur wenn Expert dies erlaubt)
- `abort`: Run beenden, Artefakte erhalten
- `escalate_to_human`: ApprovalRequest erstellen, menschliche Entscheidung abwarten

Kein automatisches Retry ohne explizite Policy-Erlaubnis (`retry_budget` im Expert).

---

## Abbruch (cancel)

`cancel()` ist ein expliziter Zustandsübergang, kein Kill-Signal.

Ablauf:
1. Run-State → `cancelling` (intern, kein eigener Zustand nach außen)
2. Laufender Worker bekommt CancellationToken gesetzt
3. Worker speichert aktuellen Checkpoint und gibt Kontrolle zurück
4. Run-State → `cancelled`, Artefakte bleiben erhalten
5. Subprozesse in Sandbox werden nach Checkpoint sauber beendet, nicht gekillt

Timeout: Falls Worker nach konfigurierbarer Frist (default: 30s) keinen Checkpoint
liefert, wird der Run als `failed` mit `error_code: "cancel_timeout"` markiert.

---

## Persistenz

Run-State wird nach jedem Übergang persistiert — nicht nur im Memory.

Backends (konfigurierbar):
- `sqlite` (default für lokale Nutzung)
- `json_file` (für einfache Setups / Tests)
- `postgres` (für Multi-User-Setups)

Lesbarkeit: Alle State-Übergänge sind als Audit-Events in `execution_audit_events`
exportierbar (siehe `execution-audit-event-schema.md`).

---

## Wiederaufnahme

Wiederaufnahme eines Runs ist nur erlaubt wenn:
1. Run ist im Zustand `failed` oder `cancelled`
2. Der fehlgeschlagene Schritt ist idempotent, ODER
3. Menschliche Freigabe über ApprovalRequest vorliegt

Wiederaufnahme startet nicht den Run von vorne — sie setzt am gespeicherten
`failed_at_step` an. Nicht-idempotente Schritte vor diesem Punkt werden nicht wiederholt.

---

## Tests

| Testfall                                   | Erwartung                                              |
|--------------------------------------------|--------------------------------------------------------|
| Normaler Durchlauf created → completed     | Alle Timestamps gesetzt, Artefakte vorhanden           |
| Ungültiger Übergang (z.B. completed→running) | InvalidTransitionError, kein State-Update             |
| Cancel während running                     | State → cancelled, Checkpoint erhalten                 |
| Cancel-Timeout nach 30s                    | State → failed, error_code = "cancel_timeout"          |
| Fehler in planning mit retry-Option        | recovery_options enthält "retry"                       |
| Approval abgelaufen in waiting_for_approval| State → failed, error_code = "approval_expired"        |
| Wiederaufnahme ohne Idempotenz-Check       | Ablehnung ohne menschliche Freigabe                    |
| Persistenz: Run nach Neustart lesbar       | State korrekt aus DB/JSON wiederhergestellt            |
