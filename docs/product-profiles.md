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

### developer-local

**Ziel:** schneller lokaler Entwickler-Loop.

- Diagnostik und lokale Runtimes sichtbar.
- Review/Governance bleibt vorhanden, aber mit developer-fast-path Defaults.

### team-controlled

**Ziel:** Team-Umgebung mit klaren Policies, nachvollziehbaren Freigaben und Audit.

- Explizite Governance-/Review-Defaults.
- Wiederholbare Compose-/Test-Setups.

### secure-enterprise

**Ziel:** strikte Kontrollgrenzen, Auditierbarkeit, geringe Angriffs- und Datenflaeche.

- Minimierte Exposure, konservative Tool- und Execution-Grenzen.
- Governance wird als Produktentscheidung sichtbar gemacht.

## Implementierungs-Hinweis

Im Code werden diese Profile derzeit als zusaetzliche `runtime_profile`-Eintraege modelliert (additiv, kompatibel). Die bestehenden Profile (`local-dev`, `trusted-lab`, `compose-safe`, `distributed-strict`) bleiben erhalten.

