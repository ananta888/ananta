# Operator TUI: Drei-Wege-Diff (`:diff3`)

Der Drei-Wege-Diff-Modus erlaubt drei parallel konfigurierbare Panels (**A/B/C**).

## Schnellstart

1. `:diff3`
2. `:diff3 panel A current`
3. `:diff3 panel B current --mode summary`
4. `:diff3 panel C ai review`
5. `:diff3 ai run review`

## Panel-Befehle

- `:diff3 panel <A|B|C> current [--mode unified|summary|side_by_side|files_only|hunks_only]`
- `:diff3 panel <A|B|C> output <output-artifact-id>`
- `:diff3 panel <A|B|C> ai <review|explain|risk|tests|patch|chat>`
- `:diff3 panel <A|B|C> mode <render-mode>`
- `:diff3 panel <A|B|C> filter path_filter=... status_filter=... search_text=...`

## Navigation

- `:diff3 focus <A|B|C>`
- `:diff3 scroll up|down|pageup|pagedown`
- `:diff3 sync on|off`

## KI-Modi

- `review`: strukturierte Review-Findings
- `explain`: Änderungen erklären
- `risk`: Risiko- und Regressionssicht
- `tests`: Testvorschläge
- `patch`: Patch-Vorschläge als Artefakt
- `chat`: freie, kontextbezogene Q&A

Ausführung:

- `:diff3 ai <mode>` setzt den aktiven KI-Modus
- `:diff3 ai run [mode]` führt den Modus aus

## Patch-Sicherheit

`patch`-Antworten werden als **Output-Artefakt** (`patch_suggestion`) registriert.  
Sie werden **nicht automatisch angewendet**.

