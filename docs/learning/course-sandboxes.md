# Course Sandboxes

## Ziel

Praktische Uebungen laufen in begrenzten, ruecksetzbaren Umgebungen mit minimalen Rechten.

## Sandbox-Regeln

- Jede Uebung hat einen klaren Scope (Dateien, Tools, Runtime).
- Zugriff nur auf explizit freigegebene Trainingsartefakte.
- Shell-/Tool-Zugriff ist nach Risikostufe begrenzt.
- Nach Uebung: Reset oder Loeschung der Sandbox moeglich.

## Sicherheitsstufen

1. **Low risk**: read-only Analyse, Demo-Daten.
2. **Medium risk**: begrenzte Schreibrechte im isolierten Workspace.
3. **High risk**: nur mit Review/Approval, striktes Logging und enge Timeouts.

## No-leak-Policy

- Keine Production-Secrets
- Keine Production-Daten
- Keine ungepruefte Rechteeskalation zwischen Uebungen
