# Nagabanda: reproduzierbare Stellungen

Die Beispiele benutzen die [Feldnummern der Testkarte](paper_map_19_hex.md).
Nicht genannte Figuren und Ashrams fehlen jeweils. Die Core-Tests bauen die
Stellungen direkt im Speicher auf; sie sind keine zusätzliche Startvariante.

| Stellung | Ergebnis | Begründung |
| --- | --- | --- |
| Sonne-Rishi H10, sonst leer | keine | Alle sechs Nachbarfelder sind offen |
| Sonne-Rishi H10, Mond-Deva H11 | teilweise | Nur H11 ist blockiert |
| Sonne-Rishi H10, Mond-Naga H11 | teilweise | H11 ist besetzt; H06 und H15 stehen unter Naga-Einfluss |
| Sonne-Rishi H10, Mond-Figuren H05/H06/H09/H11/H14/H15 | voll | Alle sechs Nachbarfelder blockiert; keine Flucht, −1 Stärke |
| Vorige Stellung plus Sonne-Ashram H10 | teilweise | Der eigene Ashram bricht volle Nagabanda; Nachbarn bleiben belegt |
| Vorige Stellung stattdessen mit Sonne-Ashram H09 | teilweise | Auch ein angrenzender eigener Ashram schützt |
| Sonne-Rishi H01, Mond-Figuren H02/H04/H05 | voll | Am Rand zählen nur die drei vorhandenen Nachbarn |
| Sonne-Rishi H01, Sonne-Figuren H02/H04/H05 | keine | Eigene Figuren erzeugen keine Einkreisung; trotzdem kein freies Fluchtfeld |

Die Flucht wird **vor** jeder Änderung durch den Angriff ermittelt. Ein Rishi
auf H10, angegriffen von H09, flieht bei sonst freien Feldern nach H05. Ein
zweiter Angriff vor seinem nächsten eigenen Zug löst regulären Kampf aus.

Ausführbare Fälle stehen in
[`game.test.cjs`](../../../packages/ananta-game-core/test/game.test.cjs).
