# Trigger und Automation
<!-- COSMOS-003 -->

## Prinzip

Trigger erzeugen ausschließlich neutrale TriggerEvents. Sie starten keine Worker-Aktionen
direkt. Der Hub mappt TriggerEvents auf Blueprints und Experts nach Policy (`trigger_policy.yaml`).

Externe Systeme (GitHub, Webhooks, CI) bekommen keine Agenten-Rechte — sie liefern nur
Signale. Was daraus folgt, entscheidet allein der Hub.

---

## Trigger-Quellen

| source                | Beschreibung                                              | Auth-Anforderung     |
|-----------------------|-----------------------------------------------------------|----------------------|
| manual_ui             | Operator startet Run manuell über TUI/Web                 | Session-Auth         |
| cli_command           | `ananta run ...` auf der Kommandozeile                    | lokaler User         |
| github_issue          | GitHub Issue erstellt oder gelabelt                       | Webhook + HMAC       |
| github_pull_request   | PR geöffnet, synchronisiert oder gelabelt                 | Webhook + HMAC       |
| github_check_failed   | CI-Check fehlgeschlagen                                   | Webhook + HMAC       |
| scheduled_job         | Zeitgesteuerter Trigger (Cron-ähnlich)                    | Hub-intern           |
| webhook               | Generischer eingehender Webhook                           | HMAC oder Token      |
| local_file_change     | Lokale Dateiänderung (inotify / fswatch)                  | lokal, kein Netzwerk |
| blueprint_event       | Ein Blueprint-Schritt erzeugt ein Follow-up-Event         | Hub-intern           |

---

## TriggerEvent Schema

```python
@dataclass
class TriggerEvent:
    trigger_id: str         # uuid, unveränderlich
    source: str             # Wert aus Trigger-Quellen-Tabelle
    payload_hash: str       # sha256 des Payloads (nicht der Payload selbst)
    scope_hint: str | None  # z.B. repo-Pfad, Projekt-ID — ungeprüfter Hint
    created_at: float       # unix timestamp
    rate_limit_key: str     # für Rate-Limiting (z.B. "webhook:repo:main")
    validated: bool         # True nur nach HMAC/Token-Verifikation
    raw_payload_ref: str | None  # artifact_id für "internal" artifact, nie direkt eingebettet
```

Payloads werden nicht im TriggerEvent gespeichert — nur als `internal`-Artefakt
mit Referenz. Sensible Felder (Tokens, Secrets) werden vor Speicherung redigiert.

---

## Rate-Limiting

Jeder Trigger-Typ hat ein konfigurierbares Rate-Limit in `trigger_policy.yaml`:

```yaml
rate_limits:
  webhook:
    max_per_minute: 5
    burst: 10
  github_pull_request:
    max_per_minute: 20
  scheduled_job:
    max_per_hour: 60
  manual_ui:
    max_per_minute: 30
```

Überschrittene Rate-Limits: TriggerEvent wird abgelehnt und als Audit-Event
mit `error_code: "rate_limit_exceeded"` gespeichert. Kein Retry ohne Backoff.

---

## Webhook-Sicherheit

Externe Webhooks werden nur akzeptiert wenn:
1. HMAC-SHA256-Signatur im Header stimmt, ODER
2. Bearer-Token in Allowlist des Projekts

Fehlende oder ungültige Signatur → HTTP 403, Audit-Event `"webhook_rejected"`.

Geheime Tokens werden nie im TriggerEvent gespeichert. Nur `validated: true/false`.

---

## Hub-Mapping (trigger_policy.yaml)

```yaml
trigger_mappings:
  - source: github_pull_request
    payload_pattern:
      action: "opened"
      label: "ananta:review"
    allowed_blueprint_ids:
      - pr_deep_review
      - pr_risk_analysis
    scope: project
    enabled: true

  - source: github_check_failed
    payload_pattern:
      conclusion: "failure"
    allowed_blueprint_ids:
      - failed_ci_debug
    scope: project
    enabled: true

  - source: scheduled_job
    schedule: "0 3 * * *"    # täglich 3:00 Uhr
    allowed_blueprint_ids:
      - nightly_dependency_check
    scope: global
    enabled: false            # deaktiviert bis explizit eingeschaltet
```

Matching: source + payload_pattern (JSON-Subset-Match). Kein Mapping → TriggerEvent
wird ignoriert, Audit-Event `"trigger_no_mapping"`.

---

## Deaktivierung

Jeder Trigger kann deaktiviert werden:
- Global: `enabled: false` in `trigger_policy.yaml`
- Pro Projekt: Override in Projektconfig
- Laufzeit: Hub-Admin-Befehl ohne Neustart

Deaktivierte Trigger erzeugen weiterhin ein Audit-Event (`"trigger_disabled_ignored"`),
damit der Operator sieht, dass ein Signal ankam und ignoriert wurde.

---

## Tests

| Testfall                                          | Erwartung                                           |
|---------------------------------------------------|-----------------------------------------------------|
| Webhook mit korrekter HMAC                        | TriggerEvent erstellt, validated=true               |
| Webhook mit falscher HMAC                         | HTTP 403, Audit "webhook_rejected", kein Event      |
| Trigger mit Mapping → Blueprint                   | Blueprint-Run gestartet                             |
| Trigger ohne Mapping                              | Audit "trigger_no_mapping", kein Run                |
| Rate-Limit überschritten (6. Webhook in 1 Min)    | Ablehnung, Audit "rate_limit_exceeded"              |
| Deaktivierter Trigger sendet Signal               | Audit "trigger_disabled_ignored", kein Run          |
| blueprint_event erzeugt Follow-up-Event           | Neuer TriggerEvent mit source=blueprint_event       |
| scheduled_job außerhalb Cron-Fenster              | Kein Event                                          |
