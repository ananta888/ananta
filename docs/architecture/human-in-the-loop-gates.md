# Human-in-the-loop Policy Gates
<!-- COSMOS-006 -->

## Zweck

Riskante Aktionen werden nicht automatisch ausgeführt. Jede solche Aktion erzeugt einen
`ApprovalRequest`, der von einem berechtigten Operator beantwortet werden muss.
Fehlende oder abgelaufene Approvals blockieren die Aktion hart — kein Fallback auf "allow".

---

## Gate-Typen

| gate_type              | Auslöser                                              | Standard-Risiko |
|------------------------|-------------------------------------------------------|-----------------|
| apply_diff             | Worker will Dateien ändern                            | medium          |
| delete_file            | Worker will Datei/Verzeichnis löschen                 | high            |
| run_network_tool       | Worker will Netzwerkzugriff                           | medium          |
| send_context_external  | Kontext wird an externes System gesendet              | high            |
| create_pull_request    | PR auf Remote wird erstellt                           | medium          |
| merge_pull_request     | PR wird gemergt (kein Auto-Merge)                     | critical        |
| rerun_ci               | CI-Pipeline wird neu gestartet                        | low             |
| access_secret_ref      | Worker will secret_ref-Artefakt verwenden             | critical        |
| deploy_or_release      | Deployment oder Release-Aktion                        | critical        |

Policy kann pro Projekt das Risiko-Level einzelner Gate-Typen erhöhen, nicht senken.

---

## ApprovalRequest Schema

```python
@dataclass
class ApprovalRequest:
    gate_id: str          # uuid, unveränderlich
    run_id: str
    gate_type: str        # aus Gate-Typen-Tabelle
    risk_level: str       # "low" | "medium" | "high" | "critical"
    required_role: str    # "operator" | "maintainer" | "owner"
    reason: str           # maschinenlesbare Begründung (kein freier Text)
    artifacts: list[str]  # artifact_ids die der Approval-Empfänger einsehen kann
    expires_at: float     # unix timestamp — Ablaufdatum der Anfrage
    created_at: float
    created_by: str       # worker_id oder "hub"
```

```python
@dataclass
class ApprovalRecord:
    gate_id: str
    decision: str         # "approved" | "denied"
    decided_by: str       # Nutzer-ID
    decided_at: float
    reason: str | None    # optionale Begründung des Operators
    # ApprovalRecord ist unveränderlich nach Erstellung
```

---

## Entscheidungsregel

```
Run erreicht gate-pflichtige Aktion
  │
  ▼
ApprovalRequest erstellen → Run-State: waiting_for_approval
  │
  ├─► Operator approved vor expires_at
  │     → Run-State: running, Aktion wird ausgeführt
  │     → ApprovalRecord gespeichert
  │
  ├─► Operator denied
  │     → Run-State: failed, error_code: "gate_denied"
  │     → ApprovalRecord gespeichert
  │
  └─► expires_at überschritten ohne Entscheidung
        → Run-State: failed, error_code: "approval_expired"
        → ApprovalRecord mit decision: "expired" gespeichert
```

Es gibt keinen Bypass. Kein Worker kann eine Approval-Anforderung umgehen.
Policy kann keine Gate-Typen mit `risk_level: "critical"` abschalten.

---

## Ablauf im Laufzeitsystem

1. Worker meldet beabsichtigte Aktion an Hub (nicht direkt ausführen).
2. Hub prüft Policy: Gate-Typ in Projekt-Policy enthalten?
3. Hub erstellt `ApprovalRequest` und setzt Run in `waiting_for_approval`.
4. UI/CLI zeigt ApprovalRequest mit Kontext-Artefakten an.
5. Operator entscheidet; Hub erhält Entscheidung.
6. Hub erstellt unveränderlichen `ApprovalRecord` (artifact_type: `approval_record`).
7. Run setzt fort (approved) oder schlägt fehl (denied/expired).

---

## UI und CLI

ApprovalRequests erscheinen im Tracking Viewer (TUI/Web) mit:
- gate_type, risk_level, reason
- Links zu den zugehörigen Artefakten (z.B. Diff-Vorschau)
- Verbleibende Zeit bis expires_at
- Schaltflächen: Approve / Deny

CLI:
```bash
ananta gate list --run <run_id>
ananta gate approve <gate_id> [--reason "..."]
ananta gate deny <gate_id> [--reason "..."]
```

---

## Audit

`ApprovalRecord` wird als Artefakt mit `policy_class: "internal"` gespeichert und
ist von der Retention-Policy ausgenommen. Jede Entscheidung ist nachvollziehbar:
- Wer hat entschieden (decided_by)
- Wann (decided_at)
- Warum (reason, optional)
- Welche Artefakte lagen vor (gate_id → ApprovalRequest.artifacts)

---

## Tests

| Testfall                                           | Erwartung                                           |
|----------------------------------------------------|-----------------------------------------------------|
| Aktion ohne Approval-Request                       | Hub blockiert, ApprovalRequest wird erstellt        |
| Operator approved vor Ablauf                       | Run fortsetzt, ApprovalRecord decision="approved"   |
| Operator denied                                    | Run failed, error_code="gate_denied"                |
| expires_at überschritten                           | Run failed, error_code="approval_expired"           |
| Worker versucht Aktion nach denied Gate            | Aktion blockiert, kein zweiter ApprovalRequest      |
| Gate-Typ nicht in Policy                           | Run failed sofort, kein ApprovalRequest             |
| critical Gate via Policy deaktiviert               | PolicyConfigError beim Laden                        |
| ApprovalRecord nach Retention-Policy               | Bleibt erhalten (nie gelöscht)                      |
