# Ananta: Kurzregel des spielbaren Prototyps

Regelstand: `brutal-mvp-v1`. Zwei Personen spielen abwechselnd an einem Gerät.
Die vorhandenen Konzepte legen Einheiten, AP, Deva, Rishi und Nagabanda fest.
Startaufstellung, Zahlenwerte, Kampfausgang, Fluchtziel und Partieende sind hier
vorläufige Designentscheidungen für einen vollständigen, testbaren Prototyp.
Die automatisierten Partien belegen keine menschlich getestete Spielbalance.

## Aufbau und Ziel

Verwende die [19-Hex-Karte](paper_map_19_hex.md). Sonne startet oben, Mond unten.
Jede Seite besitzt einen Ashram, je einen Naga, Rishi und Deva und **4 Soma**.
Sonne beginnt. Eine Runde besteht aus je einem Zug von Sonne und Mond.
Meru und die drei Lokas sind im Prototyp reine Orientierung ohne Sonderbonus.

Gewonnen hat, wessen Figur den gegnerischen Ashram betritt, auch als Ergebnis
eines Angriffs oder einer gegnerischen Flucht. Die Partie endet sofort.
Nach dem beendeten Mond-Zug der Runde 40 endet sie andernfalls unentschieden.

## Dein Zug

Du hast **6 AP**. Ab deinem zweiten eigenen Zug erhältst du **2 Soma** je eigenem
Ashram. Beide Spieler starten ihren ersten Zug mit genau 4 Soma.
Übrige AP verfallen beim Zugende, Soma bleibt erhalten. Du darfst vorzeitig passen.

| Aktion | Kosten | Voraussetzung |
| --- | --- | --- |
| Bewegen | 1 AP | Auf ein freies, passierbares direktes Nachbarfeld |
| Angreifen | 2 AP | Gegner auf einem direkten Nachbarfeld; höchstens einmal je Figur und eigenem Zug |
| Rekrutieren | 2 AP + Soma | Freies Feld auf oder direkt neben dem eigenen Ashram; kein gegnerischer Ashram |
| Zug beenden | frei | Übergibt an den anderen Spieler |

Eine Figur darf mehrfach ziehen, solange AP vorhanden sind. Figuren können
weder gestapelt werden noch über andere Figuren springen. Naga-Einfluss verbietet
gewöhnliche Bewegung nicht; er zählt für Einkreisung und Flucht.
Neue Figuren werden erst zu Beginn deines nächsten Zuges aktiv, auch ihre Flucht.
Ihr Naga-Einfluss gilt bereits ab dem Aufstellen.

| Figur | Stärke | Rekrutierung | Besonderheit |
| --- | --- | --- | --- |
| Naga | 2 | 2 Soma | Einfluss auf alle direkten Nachbarfelder |
| Rishi | 1 | 2 Soma | Automatische Flucht bei einem Angriff, falls möglich |
| Deva | 3 | 4 Soma | Darf in einem eigenen Zug bewegen **oder** angreifen |

## Kampf und Flucht

Zuerst wird eine mögliche Rishi-Flucht geprüft. Rishi flieht höchstens einmal
zwischen zwei eigenen Zuganfängen. Das Ziel muss frei, passierbar, unmittelbar
benachbart, außerhalb gegnerischen Naga-Einflusses und kein gegnerischer Ashram
sein. Unter mehreren Zielen gilt die kleinste Feldnummer. Der Angreifer rückt
auf das geräumte Feld nach. Volle Nagabanda verhindert die Flucht.

Ohne Flucht werden Stärken verglichen: Volle Nagabanda gibt −1 (Minimum 0).
Ein Verteidiger direkt auf seinem eigenen Ashram erhält +1. Die schwächere Figur
wird entfernt; gewinnt der Angreifer, rückt er vor. Bei Gleichstand bleiben beide
Figuren stehen. Ein Angriff verbraucht seine 2 AP und die Angriffsmöglichkeit
auch bei Flucht oder Gleichstand. Devas Vorrücken nach einem Angriff ist Teil
des Kampfes; danach ist keine gewöhnliche Bewegung erlaubt.

## Nagabanda

Prüfe nur tatsächlich vorhandene Nachbarfelder, keine Felder jenseits des Randes.
Ein Nachbarfeld ist blockiert durch eine gegnerische Figur, gegnerischen
Naga-Einfluss oder unpassierbares Terrain. Eigene Figuren allein blockieren
nicht für Nagabanda, belegen aber weiterhin ihr Feld und verhindern eine Flucht dorthin.

Kein blockiertes Feld bedeutet keine Einkreisung; einige bedeuten teilweise
Einkreisung. Sind alle blockiert, gilt volle Nagabanda. Ein eigener Ashram auf
dem Feld oder direkt daneben verhindert volle Nagabanda. Er schafft jedoch kein
freies Fluchtfeld. Teilweise Einkreisung hat keinen Stärkeabzug.
Die Startkarte enthält kein unpassierbares Terrain; dieses wird als Regelrandfall getestet.

Die [Fallbeispiele](nagabanda_examples.md) zeigen die Grenzfälle. Economy, Arenen,
Online-Spiel, KI-Figuren und Hardware sind weiterhin spätere Erweiterungen.
