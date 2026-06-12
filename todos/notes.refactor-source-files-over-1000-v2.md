# Session-Notizen: refactor-source-files-over-1000-v2

Arbeitsnotizen zum Track `todo.refactor-source-files-over-1000-v2.json`.
Task-Status wird ausschließlich im Todo-JSON gepflegt; diese Datei hält
Arbeitsweise, Patterns und Kontext fest, die nicht ins JSON passen.

## Ziel

- Alle Python-Quelldateien über 1000 Zeilen unter die Source-Line-Limit-Policy bringen
- V2-M1: 14 agent/routes-Dateien (abgeschlossen); V2-M2: 5 Dateien in `client_surfaces/operator_tui/`

## Constraints & Präferenzen

- Kein `git add .` — Dateien einzeln benennen
- Public API kompatibel halten (Import-Ketten, Klassenmethoden, Module-Level-Re-Exports)
- Tests vor jedem Commit-Batch laufen lassen
- Zirkuläre Imports auf Modulebene vermeiden — pure statische Helper lieber als Module-Level-Funktionen ins extrahierte Submodul kopieren
- Beim Splitten gemeinsame Helper zuerst im Hauptmodul halten, damit Submodule sie ohne Zirkularität importieren können
- Extrahierte Namen im Hauptmodul re-exportieren
- Commits: ein Commit pro logischer SPLIT-Gruppierung; Scope-Präfix je Subsystem
- Namenskonvention für Submodule: `_interactive_*` für Extraktionen aus `interactive.py`

## Bewährte Patterns

- **Module-level extraction first**: sicherste Variante mit geringstem Zirkular-Import-Risiko
- **Thin-Wrapper-Pattern**: extrahierte Funktionen behalten einen 2-zeiligen delegierenden Wrapper im Hauptmodul, damit öffentliche Import-Ketten unverändert funktionieren
- **Helper-first-Pattern**: gemeinsame Helper/Konstanten zuerst im Hauptmodul definieren, Submodule danach importieren
- **Copy-static-helpers-Pattern**: würde eine Methoden-Extraktion einen zirkulären Import erzeugen, den statischen Helper als Module-Level-Funktion ins Submodul kopieren
- **Same-Commit-Policy**: eng verwandte SPLITs dürfen zusammen committet werden

## Stand (2026-06-12)

### Erledigt
- **V2-M1 komplett** (Commit `72ee18c56`): 14 große agent/routes-Dateien gesplittet — alle unter 1000 Zeilen; dabei `config_defaults.py`-Fix (fehlendes `import os`)
- **SPLIT-115 (teilweise)**: 5 Artifact-Sub-Mode-Renderer (~500 Zeilen) aus `_renderer_content.py` (1888→1388) nach `_renderer_content_artifact.py` extrahiert; 660 TUI-Tests grün (Commit `a84af53b9`)
- **SPLIT-116**: Config-Helper (~250 Zeilen) aus `ai_snake_config_view.py` (1113→864) nach `_ai_snake_config_helpers.py`; 660 TUI-Tests grün (Commit `a84af53b9`)
- **SPLIT-117**: Browser/Window-Controller (7 Methoden) nach `_interactive_window.py` und alle Chat/Snapshot/Long-Message-Methoden (~45 Methoden) nach `_interactive_chat.py` aus `interactive.py` (1753→870); 215 TUI-Tests grün (Commit `a84af53b9`)
- **SPLIT-118**: 18 Tutor-Methoden aus `snake_ops_mixin.py` (1285→935) nach `_snake_ops_tutor.py`; dabei Bugfix in `advance_guided_tour_now` (Commit `2e7c84ec3`)

### Offen
- **SPLIT-115 (Rest)**: `_renderer_content.py` hat noch 1388 Zeilen — weiterer Split nötig
- **SPLIT-119**: `snake_tick_mixin.py` (1179 Zeilen)
- Danach: V2-M2 abschließen, Todo-JSON aktualisieren

### Hinweis zur Commit-Historie
Commit `a84af53b9` ("Quarantine expired TUI snake heuristic proposals …") enthält neben der
Heuristik-Quarantäne auch die SPLIT-115/116/117-Extraktionen; die Message untertreibt den
Umfang. Die Arbeit entstand vor SPLIT-118 (`2e7c84ec3`), wurde aber erst danach committet —
daher wirkte der dort mitcommittete AGENTS.md-Stand veraltet.
