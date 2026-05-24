# Browser-Use Rollout Runbook

## Zweck
Dieses Runbook beschreibt die sichere Aktivierung des `browser_use`-Backends im bestehenden Hub-Worker-Modell.

## Architekturgrenzen
- Hub bleibt Eigentümer von Routing, Policy und Task-Queue.
- Browser-Ausführung erfolgt als delegierter Worker-Pfad.
- Keine Worker-zu-Worker-Orchestrierung.

## Enablement
1. `research_backend.provider` auf `browser_use` setzen (oder pro Task explizit auswählen).
2. `research_backend.providers.browser_use.enabled=true` setzen.
3. Pro Task `browser_config` mit folgenden Pflichtfeldern setzen:
- `allowed_domains`
- `max_actions`
- `timeout_seconds`
- `download_policy`
- `auth_policy`
- `screenshot_policy`

## Beispielkonfiguration
```yaml
research_backend:
  provider: browser_use
  providers:
    browser_use:
      enabled: true
      mode: native
      command: "bash -lc true"
      timeout_seconds: 120
```

## Preflight und Troubleshooting
Preflight wird über die bestehende Config-Read-Model-Ausgabe sichtbar.

Typische Fehlerbilder:
- `browser_backend_disabled`: Backend ist nicht aktiviert.
- `browser_backend_command_missing`: Kein Ausführungskommando konfiguriert.
- `browser_backend_binary_missing`: Kommando-Binary ist nicht verfügbar.
- `domain_not_allowed`: URL verletzt Domain-Allowlist.

## Policy Tuning
- Domains strikt minimieren (`allowed_domains`).
- `max_actions` klein halten und task-spezifisch erhöhen.
- Standard `download_policy=deny`, nur bei Bedarf `whitelist` oder `bounded_output_dir`.
- `auth_policy=none` als Default, nur explizit `explicit_opt_in` setzen.

## Migration
Phasenweise, rückwärtskompatibel:
1. Deploy mit deaktiviertem `browser_use`.
2. Preflight und Diagnostik prüfen.
3. Pilot-Tasks mit klaren Domain-Policies aktivieren.
4. Schrittweise auf weitere Research-Tasks erweitern.

Bestehende Nicht-Browser-Flows bleiben unverändert und lauffähig.

## Rollback (Dry-Run und Echtbetrieb)
Rollback-Schritte:
1. `research_backend.providers.browser_use.enabled=false`.
2. `research_backend.provider` zurück auf bisherigen Provider (z. B. `deerflow`).
3. Offene Browser-Tasks auf Standard-Research-Flow rerouten.

Dry-Run-Checkliste:
- Konfigurationswechsel ohne Neustart validieren.
- Preflight meldet Browser-Backend als disabled.
- Diagnostik zeigt keine neuen Browser-Calls.

## Auditing und Nachvollziehbarkeit
Für Browser-Ausführung werden Audit-Events protokolliert:
- `browser_route_selected`
- `browser_policy_checked`
- `browser_policy_blocked`
- `browser_action_executed`
- `browser_artifact_verified`
- `browser_fallback_used`

## Verknüpfung mit Projektregeln
Dieses Runbook beachtet die Architekturvorgaben aus [AGENTS.md](/home/krusty/ananta/AGENTS.md), insbesondere Hub-Kontrollhoheit, additive Evolution und Security-Guardrails.
