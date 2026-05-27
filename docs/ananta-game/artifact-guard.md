# ArtifactGuard

ArtifactGuard koppelt Task-Abschluss an verifizierbare Evidence.

## Regeln

1. Ohne Evidence kein verifizierter Abschluss.
2. Fehlgeschlagene Verification markiert den Task als `failed`.
3. Veraltete Artefakte (`stale`) verhindern Completion.
4. Punkte gibt es nur fuer verifizierte Abschluesse.

## Entscheidungsmodell

- `verified` bei Evidence + erfolgreicher Verification + frischem Artefakt
- `open` bei fehlender Evidence oder stale artifacts
- `failed` bei Verification-Fehler

Damit trennt das Modell behaupteten Fortschritt von verifiziertem Fortschritt.
