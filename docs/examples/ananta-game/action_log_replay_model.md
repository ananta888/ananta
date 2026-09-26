# ActionLog und Replay v1

`createSession()` erzeugt Startstellung und leeres Protokoll.
`recordAction(session, action)` gibt eine neue Sitzung und das Aktionsergebnis
zurück. Jede formal gültige Aktion erhält eine laufende Nummer; abgelehnte
Aktionen speichern den stabilen Ablehnungsgrund und ändern den Zustand nicht.
Der Parser kopiert die Aktionsfelder, damit spätere Änderungen am Eingabeobjekt
das Protokoll nicht verändern.

`exportReplay(session)` erzeugt JSON mit `format: ananta-game-replay`,
`version: 1`, `ruleset: brutal-mvp-v1` und `log`.
`importReplay(text)` beginnt immer mit `createInitialState()`, wendet alle
Aktionen erneut an und vergleicht Annahme/Ablehnung und Fehlercode.
Unbekannte Regelversionen, Zusatzfelder, falsche Sequenzen und widersprüchliche
Ergebnisse führen zu einem begrenzten Fehler. Ein Import ersetzt die aktuelle
UI-Partie erst nach vollständiger erfolgreicher Prüfung.

Änderungen an Startstellung, Balancewerten oder Mechaniken brauchen eine neue
Regelversion. Vorhandene Replays dürfen nicht stillschweigend unter geänderten
Regeln interpretiert werden.

Grenzen: maximal 2.048 Protokolleinträge und 1.000.000 Zeichen beim Import.
Ein volles Protokoll akzeptiert keine weiteren Aktionen. Spielfelder, Spieler,
Ressourcen oder Gewinner können nicht separat importiert werden.

Das Protokoll ermöglicht Reproduzierbarkeit, aber **keine Authentizitätsprüfung**:
Ein vollständig regelkonform umgeschriebenes Protokoll beschreibt eine andere
gültige Partie. Es enthält keine Signaturen und keine Hub-Evidence-Identitäten.
Online-Transport und signierte Mehrspielerprotokolle bleiben spätere Module.

Ein deterministischer vollständiger Beispielablauf ist im Core-Test enthalten:
Sonne-Deva H05 → H10 → H14 → H18, beide Seiten beenden ihren Zug, Sonne-Deva
greift Mond-Rishi auf H19 an und gewinnt. Dieses absichtliche Passen des Gegners
prüft den technischen Abschluss, nicht die Stärke einer Strategie.
