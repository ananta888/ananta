# Arbeitsanweisung für einen Agenten: Ananta-Todo-JSON aus generischem Präsentations-Template ableiten

Du arbeitest im Repository **ananta**. Ziel ist eine **quellcodeabgeglichene, ausführliche Todo-JSON** für ein generisches HTML/JavaScript-Präsentationssystem, das aus der vorhandenen Unterrichtspräsentation abstrahiert wird.

## Eingaben

Nutze als fachliches Muster die Datei `generic-presentation-template.html` und, falls vorhanden, die konkrete Ausgangspräsentation `agenten-n8n-praesentation.html`.

## Grundidee

Erstelle keine lose Wunschliste. Erstelle eine Todo-JSON, die direkt von einem Implementierungsagenten abgearbeitet werden kann. Jeder Eintrag muss gegen den aktuellen Ananta-Quellcode geprüft sein und konkrete Dateien, Komponenten, Services, Tests und Akzeptanzkriterien nennen.

Das Ziel-Feature für Ananta:

- generischer Präsentations-Renderer für Unterricht/Workshops
- HTML/JS-Export oder Einbettung in die Angular-App
- Folienmodell aus JSON/Markdown/Ananta-Konfiguration
- Dozentenmodus mit drei Grundmodi
- zusätzliche Ein/Aus-Schalter für Fehlerbuttons, rote Markierung und Erklärungen
- versteckte Fehler mit lokalen Erklärungen direkt an der Stelle
- Timer rechts oben, minimierbar/aufklappbar
- lokale Uhrzeit, Countdown, Alarmmodus, Quittieren, +5/+15 Minuten
- Folienzeit-Tracking pro Besuch, auch bei Vor-/Zurücknavigation
- Browser-Auswertung und CSV-Export
- Dozentenfolie/Fehlerübersicht optional generierbar
- spätere Wiederverwendung für Ananta-Schulungen, CodeCompass, n8n, AI-Agenten und interne Module

## Vorgehen im Repository

1. Prüfe zuerst die Projektstruktur.
   - Suche Angular-App, Komponenten, Services, Routing, vorhandene Presentation-/Markdown-/Editor-Funktionalität.
   - Suche vorhandene Todo-JSON-Konventionen im Repo, besonders unter `/todos`.
   - Suche CodeCompass-/Visual-Editor-/Training-/Docs-nahe Stellen, falls das Feature dort angebunden werden soll.

2. Ermittle vorhandene technische Grundlagen.
   - Gibt es bereits Markdown-Renderer?
   - Gibt es bereits HTML-Sandbox/Preview?
   - Gibt es bereits JSON-Schema-Validierung?
   - Gibt es bestehende Export-Funktionen?
   - Gibt es Services für LocalStorage, Settings, Telemetrie oder Unterrichtsmodi?
   - Gibt es Tests für Angular-Komponenten oder Services?

3. Leite daraus realistische Implementierungsaufgaben ab.
   - Nicht pauschal „neue Komponente bauen“, wenn vorhandene Komponenten erweiterbar sind.
   - Nicht behaupten, dass Funktionen existieren, ohne konkrete Datei/Codepfad zu nennen.
   - Wenn etwas fehlt, explizit als neu anzulegende Datei kennzeichnen.

## Erwartete Todo-JSON-Struktur

Lege die JSON im passenden Todo-Ordner ab, zum Beispiel:

`/todos/ananta-generic-presentation-template.todo.json`

Nutze folgende Struktur:

```json
{
  "id": "ananta-generic-presentation-template",
  "title": "Generisches HTML/JS Präsentationssystem mit Dozentenmodus und Folientracking",
  "status": "planned",
  "source_basis": [
    "generic-presentation-template.html",
    "agenten-n8n-praesentation.html"
  ],
  "repo_alignment": {
    "checked_paths": [],
    "reusable_components": [],
    "new_components_needed": [],
    "risks_or_unknowns": []
  },
  "acceptance_summary": [],
  "tasks": []
}
```

Jeder Task muss mindestens enthalten:

```json
{
  "id": "T001",
  "title": "Kurzer Task-Titel",
  "type": "analysis|implementation|test|docs|schema|ux|security",
  "priority": "high|medium|low",
  "repo_evidence": {
    "checked_files": [],
    "existing_patterns": [],
    "gap": ""
  },
  "implementation": {
    "files_to_create": [],
    "files_to_modify": [],
    "notes": []
  },
  "acceptance_criteria": [],
  "test_plan": [],
  "dependencies": []
}
```

## Inhaltliche Mindest-Tasks

Die Todo-JSON muss mindestens diese Bereiche abdecken:

1. **Datenmodell / Schema**
   - Folien, Titel, Kicker, Inhalt, Layout, Checkpoints
   - Fehlerobjekte mit Schwierigkeitsgrad `easy|advanced`
   - Erklärungstext, Position/Verankerung, optional Sichtbarkeit
   - Timer-/Tracking-/Export-Konfiguration

2. **Renderer / Präsentationskomponente**
   - Vollbild-Folienmodus
   - Navigation per Tastatur und Buttons
   - Fortschrittsbalken
   - responsive Darstellung
   - klarer Abgleich, ob Angular-Komponente oder standalone HTML-Export sinnvoller ist

3. **Dozentenmodus**
   - Modus 1: Vortrag, keine sichtbaren Fehlerhinweise
   - Modus 2: Dozent verdeckt, kleine `?`-Buttons ohne rote Markierung
   - Modus 3: Fehler markiert, rote Markierung plus Erklärbuttons
   - einzelne Schalter: Fehlerbuttons, rote Markierung, Erklärungen
   - Persistenz der Einstellung optional über LocalStorage

4. **Fehler-/Erklärsystem**
   - Markierung direkt im Text
   - lokale Erklärung direkt nahe der markierten Stelle
   - Fehlerübersicht/Dozentenfolie
   - mindestens fünf einfache und fünf fortgeschrittene Fehler pro generierter Unterrichtseinheit optional validierbar

5. **Timer**
   - lokale Uhrzeit
   - Countdown
   - Start/Pause/Reset/Quittieren
   - +5/+15 Minuten
   - minimieren/aufklappen
   - roter Alarmmodus
   - browserkonformer Signalton erst nach User-Interaktion
   - ansteigende Lautstärke nach 5 und 7 Minuten Überziehung

6. **Folienzeit-Tracking**
   - Start bei erster Folie
   - Abschluss eines Besuchs bei jedem Folienwechsel
   - Vorwärts und rückwärts getrennt protokollieren
   - laufende Folie live mitrechnen
   - Summen je Folie
   - einzelne Besuche
   - Reset

7. **CSV und Browser-Auswertung**
   - CSV-Export mit `Besuch;Folie;Titel;Beginn;Ende;Dauer_Sekunden;Dauer_lesbar;Navigation`
   - Browser-Tabelle ohne Download
   - Summenansicht und Einzelbesuchsansicht
   - korrekte Escape-Behandlung für Semikolon, Quotes und Zeilenumbrüche

8. **Integration in Ananta**
   - Wo wird das Feature erreichbar?
   - Wie werden Präsentationen gespeichert?
   - Wie kann CodeCompass/Docs/Markdown als Quelle dienen?
   - Wie kann ein Agent daraus Unterrichtsfolien oder TODOs erzeugen?
   - Welche Rollen/Rechte braucht ein Dozentenmodus?

9. **Tests**
   - Unit-Tests für Zeitberechnung
   - Tests für CSV-Escaping
   - Tests für Folienwechsel und Besuchszählung
   - Komponenten-Tests für Modusschalter
   - E2E-Test: Navigation vor/zurück erzeugt getrennte Tracking-Zeilen

10. **Dokumentation**
   - README oder Docs-Seite mit Beispiel-JSON
   - Anleitung für Dozenten
   - Anleitung für Agenten, wie aus Handout/Markdown eine Präsentation erzeugt wird

## Qualitätsregeln

- Jede Acceptance Criteria muss testbar sein.
- Keine unpräzisen Kriterien wie „funktioniert gut“.
- Keine erfundenen Dateipfade. Erst prüfen, dann nennen.
- Bestehende Architektur respektieren.
- Security nicht vergessen: API-Keys, lokale Speicherung, Exportdaten, HTML-Injection/XSS.
- Keine externen CDN-Abhängigkeiten, wenn Ananta offline/lokal lauffähig bleiben soll.
- Das Ergebnis muss eine gültige JSON-Datei sein.

## Gewünschte Abschlussprüfung

Nach Erstellung der Todo-JSON:

1. JSON syntaktisch validieren.
2. Prüfen, ob jede Task-ID eindeutig ist.
3. Prüfen, ob jede Task mindestens ein Acceptance-Kriterium hat.
4. Prüfen, ob alle genannten Dateien entweder existieren oder ausdrücklich unter `files_to_create` stehen.
5. Kurz im Commit-/Antworttext zusammenfassen:
   - welche Repo-Pfade geprüft wurden,
   - welche vorhandenen Ananta-Strukturen genutzt werden,
   - welche neuen Komponenten/Services nötig sind,
   - welche offenen Unsicherheiten bleiben.
