# Governance und Audit
<!-- COSMOS-021 -->

## Zweck

Definiert Rollen, Rechte und Audit-Mechanismen für Ananta. Ziel ist nicht Enterprise-SaaS-
Parität, sondern klare, überprüfbare Governance: wer darf was, und jede relevante Aktion
wird unveränderlich protokolliert.

---

## Rollen und Rechtematrix

| Rolle         | Runs starten | Artefakte lesen | Approvals erteilen | Config ändern | Integrationen ändern |
|---------------|--------------|-----------------|--------------------|---------------|----------------------|
| `owner`       | ja           | ja (alle)       | ja                 | ja            | ja                   |
| `maintainer`  | ja           | ja (alle)       | ja                 | nein          | nein                 |
| `reviewer`    | nein         | ja (nicht sensitiv) | ja (Review-Gates) | nein    | nein                 |
| `operator`    | ja           | ja (eigene Runs) | nein              | nein          | nein                 |
| `observer`    | nein         | ja (public)     | nein               | nein          | nein                 |

Rechte-Schnittmenge: Ein Nutzer mit mehreren Rollen erhält die Union der Einzelrechte.
Explizite Deny-Einträge in der Policy überschreiben rollenbasierte Grants.

---

## Audit-Event Schema (JSONL)

Jede Zeile in der Audit-Log-Datei ist ein JSON-Objekt:

```json
{
  "event_id": "evt-<uuid>",
  "timestamp": "2026-07-01T14:23:11.042Z",
  "actor": {
    "type": "user",
    "id": "operator-1",
    "role": "operator"
  },
  "action": "approval.granted",
  "target": {
    "type": "approval_gate",
    "id": "gate-<uuid>",
    "run_id": "<run_id>",
    "gate_type": "apply_diff"
  },
  "outcome": "success",
  "policy_ref": "project/default.policy.yaml#apply_diff",
  "correlation_id": "<run_id>",
  "metadata": {
    "risk_level": "medium",
    "artifact_ref": "runs/<run_id>/diff_proposal.patch"
  }
}
```

Pflichtfelder: `event_id`, `timestamp`, `actor`, `action`, `target`, `outcome`, `correlation_id`.

---

## Bekannte Action-Typen

| action                  | Beschreibung                                    |
|-------------------------|-------------------------------------------------|
| `run.created`           | Neuer Agentenlauf gestartet                     |
| `run.completed`         | Lauf abgeschlossen                              |
| `run.cancelled`         | Lauf abgebrochen                                |
| `approval.requested`    | Gate wartet auf Freigabe                        |
| `approval.granted`      | Gate freigegeben                                |
| `approval.rejected`     | Gate abgelehnt                                  |
| `artifact.created`      | Artefakt erzeugt                                |
| `artifact.accessed`     | Artefakt gelesen (sensitiv)                     |
| `policy.violation`      | Policy-Verletzung erkannt → `security_event`    |
| `config.changed`        | Konfiguration geändert                          |
| `integration.triggered` | Externe Integration ausgelöst                   |
| `secret.accessed`       | Secret-Referenz aufgelöst (kein Plaintext-Log)  |

---

## Sensitive Felder und Redaktion

- **Secrets**: Niemals im Klartext im Audit-Log. Nur `secret_ref: "<vault-key>"` wird gespeichert.
- **Prompt-Volltext**: Wird nicht als Audit-Event gespeichert. Nur Hash/Ref:
  `"prompt_ref": "sha256:<hash>"`.
- **Personenbezogene Daten**: Werden vor Export per konfigurierter Redaktions-Policy maskiert.
- **Artefakt-Inhalte**: Im Audit nur als Referenz (`artifact_ref`), nicht inline.

Redaktion wird beim Schreiben angewendet — nachträgliche Logs enthalten nie Plaintext-Secrets.

---

## Policy-Verletzungen als Security Events

Jede erkannte Policy-Verletzung erzeugt ein `policy.violation`-Event mit:

```json
{
  "action": "policy.violation",
  "event_class": "security_event",
  "target": {
    "type": "tool_call",
    "tool_id": "apply_diff",
    "expert_id": "work_dispatcher"
  },
  "outcome": "blocked",
  "violation_rule": "denied_tools.apply_diff"
}
```

Security Events werden in einem separaten Index (`audit_security.jsonl`) doppelt geschrieben
und sind unabhängig vom normalen Audit-Log konfigurierbar.

---

## Export und SIEM

```yaml
audit:
  output_path: "data/audit/audit.jsonl"
  security_output_path: "data/audit/audit_security.jsonl"
  rotation: daily
  retention_days: 90

  siem_webhook:
    enabled: false          # Default: deaktiviert
    url: "https://siem.example.com/ingest"
    auth_secret_ref: "vault/siem-token"
    format: "jsonl"         # jsonl | cef | leef
    filter_actions:
      - "policy.violation"
      - "approval.rejected"
      - "secret.accessed"
```

SIEM-Webhook ist optional und per Default deaktiviert.
Bei Webhook-Ausfall werden Events lokal gepuffert (max. 10.000 Events) und nachgesendet.

---

## Tests

| Testfall                                        | Erwartung                                           |
|-------------------------------------------------|-----------------------------------------------------|
| Observer versucht Approval zu erteilen          | 403, Audit-Event outcome=blocked                    |
| Operator startet Run — Audit-Event erzeugt      | run.created mit actor.role=operator                 |
| Secret in Artefakt referenziert                 | Audit enthält secret_ref, kein Plaintext            |
| Policy-Violation durch Expert                   | policy.violation + security_event in beiden Logs    |
| JSONL-Export geladen und validiert              | Alle Pflichtfelder vorhanden, kein Plaintext-Secret |
| Redaktion prompt_text                           | Nur sha256-Hash im Log, kein Klartext               |
