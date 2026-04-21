# Offizieller UI-Golden-Path

Ziel dieser Seite: ein einziger, offizieller Standardarbeitsweg im Web UI, der in Doku und Demo konsistent ist.

Der Golden-Path ist absichtlich simpel und beschreibt:

- Startpunkt
- primaere Aktion
- erwarteter Zwischenzustand
- sichtbares Ergebnis

## Golden Path: Goal planen und als Tasks sichtbar machen

1. **Startpunkt:** Dashboard (`/`).
2. **Primaere Aktion:** In "Planen" ein kurzes Goal eingeben (Quick Goal).
   - Beispiel: `Analysiere dieses Repository und schlage die naechsten Schritte vor.`
3. **Erwarteter Zwischenzustand:** Nach "Planen" werden Tasks erstellt und im UI sichtbar.
   - Erfolgssignal: Toast "`X Tasks erstellt`" und ein verlinktes Goal.
4. **Sichtbares Ergebnis:** Wechsel zu Board (`/board`) oder Goal-Detailansicht (`/goal/<id>`) und dort Status, Governance-Summary und Artefakte einsehen.

## Sichtbare Hauptaktionen

Das Dashboard bevorzugt diese Reihenfolge:

1. `Ziel eingeben` oder Preset waehlen.
2. `Goal planen`.
3. `Ziel pruefen`, `Aufgaben verfolgen` oder `Ergebnisse ansehen`.

Diagnose, Review und Demo bleiben sichtbar, sind aber als konkrete Use-Case-Einstiege gekennzeichnet und nicht als konkurrierende generische Startwege.

## First Run Zielwert (PRD-011)

Der First Run ist dann erfolgreich, wenn ein neuer Nutzer ab dem ersten sichtbaren UI-Zustand:

- innerhalb von **< 60 Sekunden** ein Goal absenden kann,
- ein sichtbares Erfolgssignal bekommt (Tasks erstellt, Goal-ID verlinkt),
- und den naechsten sinnvollen Schritt erkennt (Board oder Goal oeffnen).

Messpfad (manuell pruefbar):

1. UI oeffnen (Frontend erreichbar).
2. Dashboard -> Quick Goal: Preset "Repository verstehen" waehlen oder Goal eintippen.
3. Submit.
4. Zeit stoppen, sobald "`Tasks erstellt`" erscheint und ein Goal verlinkt ist.

Der Zielwert gilt als erreicht, wenn der Lite-Stack bereits laeuft und der Nutzer ohne Expertenoptionen vom Dashboard bis zum ersten sichtbaren Goal-/Task-Ergebnis kommt.
