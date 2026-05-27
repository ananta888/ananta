# Terminal Rendering (TUI) - NagaCore Hinweise

## Ziel

TUI-Rendering kann NagaCore als Guide anzeigen, ohne Sicherheitslogik in die Darstellung zu verschieben.

## Rendering-Grenzen

1. Rendering zeigt Zustandsdaten nur an, entscheidet sie aber nicht.
2. NagaCore-Ausgabe ist `guide_only` und niemals Policy-Authority.
3. Fallback auf reine Textdarstellung muss immer moeglich sein.

## Integration

- Eingabe: NagaCore `render_payload(surface="tui")`
- Ausgabe: Tutorial-Schritte/Status in Panel oder Sidecar
- Optional: Snake-Visualisierung als reine UX-Schicht

## Strategy-Game Darstellung

- GameMap muss mindestens als textuelle Listen-/Tabellenansicht darstellbar sein.
- Territorien sind nach `visibility` und `risk` unterscheidbar (z. B. blocked/redacted/high-risk).
- Graphische Backends (Sixel/Kitty) bleiben optional und duerfen den Fallback nicht ersetzen.
