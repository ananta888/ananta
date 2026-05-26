# Erklär-AI-Snake – Operator-Handbuch

Die **tutor-ai-Schlange (s-ai)** ist ein interaktiver KI-Worker im Ananta Operator TUI. Sie erklärt die Ananta-Architektur live während des Snake-Spiels, reagiert auf Spielereignisse und führt neue Operatoren durch die TUI.

---

## Konzept

Die s-ai-Schlange ist eine zweite Schlange im Snake-Spielfeld mit der Rolle `tutor`. Sie:

- kommentiert Spielereignisse (Futter, Kollision, Score-Meilensteine)
- erklärt TUI-Sektionen beim ersten Besuch
- beantwortet direkte Fragen per `:ask <frage>`
- führt durch alle Sektionen im Guided Mode
- kann von anderen Snakes Nachrichten empfangen

---

## Snake-Modus starten

```
Ctrl+S   – Snake-Modus aktivieren / deaktivieren
```

Im Snake-Modus ist das TUI in zwei Bereiche geteilt:
- **Links**: Spielfeld mit der lokalen Schlange (mint) und der s-ai-Schlange (amber)
- **Rechts**: AI-Erklärungs-Panel mit aktuellen Erklärungen, Tutorial-Fortschritt und Peer-Liste

---

## Tastenkürzel im Snake-Modus

| Taste | Aktion |
|---|---|
| `↑ ↓ ← →` | Schlange steuern |
| `Space` | Pause / Resume |
| `Enter` | Guided Tour: nächste Sektion sofort |
| `U` | Tutorial-AI umschalten |
| `O` | Mouse-Follow-Modus |
| `B` | Spielfeld-Rahmen umschalten |
| `X / C / V` | Auswählen / Kopieren / Ersetzen (TUI-Elemente) |
| `Z` | Auswahl löschen |
| `Ctrl+S` | Snake-Modus verlassen |

---

## Kommandos

### Geschwindigkeit

```
:speed <1-5>
```
Setzt die Spielgeschwindigkeit. Level 1 = langsam (333 ms/Tick), Level 5 = schnell (17 ms/Tick).

### Erklärungstiefe

```
:tutor mode overview    – kurze Sätze, allgemeine Konzepte (Standard)
:tutor mode deep        – 2-3 Sätze mit konkreten Beispielen
:tutor mode expert      – technisch mit Dateireferenzen
:tutor silent           – Idle-Kommentare deaktivieren
:tutor active           – Idle-Kommentare reaktivieren
:tutor replay <section> – Willkommenserklärung einer Sektion wiederholen
```

### Fragen stellen

```
:ask <frage>
```
Das Spiel pausiert, die AI-Schlange antwortet im Panel. Beispiel: `:ask Was ist ein Context Bundle?`

### Tutorial

```
:tutorial start intro           – Einführungs-Tutorial (9 Steps)
:tutorial start snake_mode      – Snake-Modus Tutorial (6 Steps)
:tutorial start codecompass     – CodeCompass-Workflow (7 Steps)
:tutorial guided                – Guided Tour durch alle Sektionen
:tutorial skip                  – aktuellen Step überspringen
:tutorial stop                  – Tutorial beenden
:tutorial reset <name>          – Fortschritt zurücksetzen
:tutorials                      – alle verfügbaren Tutorials auflisten
```

### Multi-Snake

```
:snakes                  – aktive Snakes anzeigen
:msg <snake-id> <text>   – Nachricht an andere Snake senden
```

---

## Guided Tour

Der Guided Mode führt automatisch durch alle 9 TUI-Sektionen:

1. `:tutorial start intro` (optional)
2. `:tutorial guided` – Guided Mode aktivieren
3. Die AI-Schlange navigiert alle 15 Sekunden zur nächsten Sektion
4. `Enter` überspringt die 15s-Wartezeit und navigiert sofort weiter
5. Nach der letzten Sektion erscheint eine Zusammenfassung und ein Hinweis auf `:tutorial start snake_mode`

---

## YAML-Format für Tutorial-Steps

Tutorial-Skripte liegen in `client_surfaces/operator_tui/tutorials/`.

```yaml
id: my_tutorial
title: Mein Tutorial
description: Kurze Beschreibung
steps:
  - id: step_01
    title: Erster Schritt
    task: Navigiere zur Goals-Sektion mit :section goals
    hint: Tipp – Shortcuts n/p wechseln Sektionen
    completion_event: section_visited
    section: goals
```

Felder:
- `id`: eindeutige Step-ID
- `title`: Anzeige im Panel (max. 40 Zeichen)
- `task`: Aufgabenbeschreibung (1 Satz)
- `hint`: optionaler Hinweis (1 Satz)
- `completion_event`: Ereignis das den Step abschließt (z.B. `food_eaten`, `section_visited`)
- `section`: TUI-Sektion die mit diesem Step verknüpft ist

---

## Erklärungstexte anpassen

Die Erklärungen liegen in `client_surfaces/operator_tui/snake_tutor_texts.yaml`. Struktur:

```yaml
events:
  food_eaten:
    overview: "Kurzer Text (max. 60 Zeichen)"
    deep: |
      Ausführlicherer Text mit Beispiel.
    expert: |
      Technisch mit Datei:Zeile Referenzen.

sections:
  goals:
    overview: "Willkommenstext für Goals-Sektion"
    deep: |
      ...
    expert: |
      ...

idle:
  - "Proaktiver Kommentar 1"
  - "Proaktiver Kommentar 2"
```

**Lint prüfen:**

```bash
python scripts/lint_tutor_texts.py
```

---

## Cast erzeugen

```bash
make cast
# oder
./scripts/generate_cast.sh
```

Erzeugt `assets/operator_tui_splash.cast` (Ziel: 55–65 Sekunden, max. 300 KB).

---

## Architektur

| Datei | Verantwortung |
|---|---|
| `interactive.py` | Snake-Tick, Event-Queue, Ask-Executor, Guided Tour |
| `renderer.py` | Split-View, AI-Panel, Pointer-Overlay, Pause-Overlay |
| `commands.py` | `:speed`, `:tutor`, `:ask`, `:tutorial`, `:snakes`, `:msg` |
| `snake_persistence.py` | Highscore, Tutor-Config, Section-Visits, Tutorial-Fortschritt |
| `snake_tutorial.py` | Tutorial-Loader, Step-Navigation, Artefakt-Erzeugung |
| `snake_tutor_texts.yaml` | Alle Erklärungstexte (Events, Sections, Idle) |
| `tutorials/` | Tutorial-Skripte als YAML |
| `agent/routes/snakes.py` | Hub-API für Snake-Registrierung (POST/GET/DELETE /snakes) |

**Game-State** wird als unveränderliches Dict in `state.header_logo_game` gespeichert.
Updates immer via `game = dict(state.header_logo_game or {})` → `state.with_updates(header_logo_game=game)`.

**Async Ask**: `_tutor_ask_executor` (ThreadPoolExecutor, max 1 Worker) + `_tutor_ask_future`.
Polling per `_poll_tutor_ask_result()` im Tick.

**Event-Queue**: `game['tutor_event_queue']` – Liste von Dicts mit `priority`, `key`, `at`. Max. 5 Einträge.
Prioritäten: collision(10) > level_up(7) > food_eaten(5) > idle(1).

---

*Dokument: max. 400 Zeilen. Sprache: Deutsch.*
