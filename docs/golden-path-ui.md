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

