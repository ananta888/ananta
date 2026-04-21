# Produktprofile (Operating Modes)

Produktprofile sind benannte Defaults fuer typische Betriebs- und Nutzungskontexte. Sie setzen Einstiegspfade und Erwartungshaltungen, ohne die Hub-Worker-Architektur zu aendern.

Wichtig:

- Der Hub bleibt Orchestrator (Control Plane).
- Worker fuehren delegierte Tasks aus.
- Profile beschreiben Defaults (z.B. Governance/Review/Exposure), keine implizite gemeinsame Runtime.

## Profile

### demo

**Ziel:** reproduzierbare Demos, klare Use-Case-Erzaehlung, minimale Setup-Friction.

- Fokus auf UI-Golden-Path und Presets.
- Explainability sichtbar, technische Drilldowns nachrangig.
- Default Governance: `balanced`.
- Nutzungskontext fuer Metriken: `demo`.
- Einstiegspfade: UI First Run, CLI First Run, `docs/golden-path-ui.md`.

### developer-local

**Ziel:** schneller lokaler Entwickler-Loop.

- Diagnostik und lokale Runtimes sichtbar.
- Review/Governance bleibt vorhanden, aber mit developer-fast-path Defaults.
- Default Governance: `safe`.
- Nutzungskontext fuer Metriken: `trial`.
- Einstiegspfade: CLI First Run, `docs/golden-path-cli.md`.

### team-controlled

**Ziel:** Team-Umgebung mit klaren Policies, nachvollziehbaren Freigaben und Audit.

- Explizite Governance-/Review-Defaults.
- Wiederholbare Compose-/Test-Setups.
- Default Governance: `balanced`.
- Nutzungskontext fuer Metriken: `production`.
- Einstiegspfade: Dashboard, Release Golden Path, Governance-Modi.

### secure-enterprise

**Ziel:** strikte Kontrollgrenzen, Auditierbarkeit, geringe Angriffs- und Datenflaeche.

- Minimierte Exposure, konservative Tool- und Execution-Grenzen.
- Governance wird als Produktentscheidung sichtbar gemacht.
- Default Governance: `strict`.
- Nutzungskontext fuer Metriken: `production`.
- Einstiegspfade: Governance-Modi, Release Golden Path.

### local-first

**Ziel:** lokale Ausfuehrung und schnelle Diagnose zuerst.

- Geeignet fuer lokale Entwicklung, Trial und Debugging.
- Default Governance: `safe`.
- Nutzungskontext fuer Metriken: `trial`.
- Einstiegspfade: CLI First Run, CLI Golden Path.

### review-first

**Ziel:** manuelle Kontrolle zuerst, bevor riskante Schritte ausgefuehrt werden.

- Geeignet fuer Teams und kontrollierte Umgebungen.
- Default Governance: `strict`.
- Nutzungskontext fuer Metriken: `production`.
- Einstiegspfade: Goal Detail, Governance-Modi, Release Golden Path.

## Effektive Defaults

Die Runtime-Profile liefern inzwischen konkrete Default-Signale:

- `default_governance_mode`: empfohlener Governance-Modus fuer das Profil.
- `usage_context`: analytische Trennung fuer `demo`, `trial` und `production`.
- `entry_paths`: konkrete UI-, CLI- oder Doku-Pfade fuer den Einstieg.

Diese Felder sind Read-Model-Signale. Harte Policy-Durchsetzung bleibt in den expliziten Policy-Bloecken, damit Profile keine versteckten Seiteneffekte erzeugen.

## Implementierungs-Hinweis

Im Code werden diese Profile derzeit als zusaetzliche `runtime_profile`-Eintraege modelliert (additiv, kompatibel). Die bestehenden Profile (`local-dev`, `trusted-lab`, `compose-safe`, `distributed-strict`) bleiben erhalten.
