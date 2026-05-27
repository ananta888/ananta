# Strategy Game TUI-Konzept

## Ziel

Die TUI zeigt dieselbe GameMap wie Web/JSON in einer terminaltauglichen Form, ohne Sicherheitslogik in die Darstellung zu verschieben.

## Mindestdarstellung

1. Textuelle Listenansicht fuer Territorien, Agenten, Policies, Context Gates und Artifacts.
2. Vergleichbare Darstellung moeglich als:
   - Raster (Karten)
   - Graph-Liste (Knoten/Kanten)
   - kompakte Tabellenansicht
3. Sichtbarkeit (`visible`, `blocked`, `hidden`, `redacted`) und Risk-Level bleiben immer lesbar.

## Rendering-Grundsätze

- Grafikbeschleunigung ist optional (Enhancement), nicht Voraussetzung.
- ANSI-only Fallback muss voll nutzbar sein.
- Sixel/Kitty-Unterstuetzung bleibt reine Darstellungsschicht.
- NagaCore darf als Guide visualisieren, aber nie Policy-Entscheidungen treffen.
