# 19-Hex-Testkarte

Axiale Koordinaten mit Radius 2. Nachbarabstände sind `(1,0)`, `(0,1)`,
`(-1,1)`, `(-1,0)`, `(0,-1)` und `(1,-1)`.
Die Feldnummern laufen je Reihe von links nach rechts.

```text
             H01  H02  H03          Obere Loka
          H04  H05  H06  H07        Obere Loka
       H08  H09  H10  H11  H12      Mittlere Loka
          H13  H14  H15  H16        Untere Loka
             H17  H18  H19          Untere Loka
```

| Felder | r | q, von links nach rechts |
| --- | --- | --- |
| H01–H03 | −2 | 0, 1, 2 |
| H04–H07 | −1 | −1, 0, 1, 2 |
| H08–H12 | 0 | −2, −1, 0, 1, 2 |
| H13–H16 | 1 | −2, −1, 0, 1 |
| H17–H19 | 2 | −2, −1, 0 |

H10 ist Meru. Alle 19 Felder sind passierbar. Es gibt keine Terrain-,
Loka- oder Meru-Boni und keine zusätzlichen Ressourcenfelder.

| Spieler | Ashram und Rishi | Naga | Deva |
| --- | --- | --- | --- |
| Sonne | H01 | H04 | H05 |
| Mond | H19 | H16 | H15 |

Die Aufstellung ist unter einer Drehung um 180° symmetrisch. Ein Ashram darf
eine Figur beherbergen. H01 hat die Nachbarn H02, H04 und H05; H10 hat H05,
H06, H09, H11, H14 und H15.

Der [Core](../../../packages/ananta-game-core/README.md) erzeugt diese Karte
deterministisch. Zum Papier-Spielen reichen sechs Figuren, zwei Ashram-Marker,
Soma-Zähler und sechs AP-Marker je Seite.
