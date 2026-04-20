# Demo-Flows (reproduzierbar)

Diese Seite liefert reproduzierbare Demo-Flows fuer die offiziellen Kern-Use-Cases. Sie ist bewusst kompakt und verweist auf den UI-/CLI-Golden-Path.

Sie ist eine Startbasis fuer PRD-002 (vollstaendige Abdeckung mit UI+CLI+API pro Use-Case).

## Flow A: Repository verstehen (UC1)

UI:
1. Dashboard -> "Ziel planen" (Quick Goal)
2. Goal: `Analysiere dieses Repository und schlage die naechsten Schritte vor.`
3. Ergebnis: Tasks erstellt, Goal oeffnen, Artefakte ansehen

CLI:
- `python -m agent.cli_goals analyze "Analysiere dieses Repository und schlage die naechsten Schritte vor"`

API (minimal):
- `POST /goals` mit `{"goal":"Analysiere dieses Repository ...","create_tasks":true}`

## Flow B: Bugfix planen (UC2)

UI:
1. Dashboard -> Preset "Bugfix planen"
2. Kurz den Bug in einem Satz ergaenzen

CLI:
- `python -m agent.cli_goals patch "Login bricht bei leerem Passwort ab, reproduziere und schlage einen kleinen Fix vor"`

## Flow C: Start/Deploy diagnostizieren (UC3)

UI:
1. Dashboard -> "Diagnostizieren"
2. Problemtext: "Frontend erreicht Hub nicht" + Logs/Fehlertext

CLI:
- `python -m agent.cli_goals diagnose "Docker frontend cannot reach hub (connection refused)"`

