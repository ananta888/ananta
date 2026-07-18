# CodeCompass Range Context Planner

`CodeCompassContextPlanner` baut aus CodeCompass-Such- und Graphdaten ein
kleines, serialisierbares `codecompass_context_bundle.v1`.

Flow:

1. Worker fragt `codecompass.plan_context` mit Query, Task-Art und Budget an.
2. Der Tool-Adapter ruft den Planner-Service auf.
3. Der Planner normalisiert Treffer zu `LocationRef`s:
   `path`, `line_start`, `line_end`, `symbol`, `reason`, `score`, `source`.
4. Das Bundle budgetiert Ranges deterministisch und leitet `patch_targets`
   ab.
5. Der Worker-Mutation-Loop materialisiert Top-Ranges über
   `repo.read_file_range`.
6. Änderungen laufen über `patch_request`, danach `workspace.diff` und Tests.

Verantwortungsgrenzen:

- Hub: Registry, Policy, Routing, Audit und Feedback-Loop.
- Planner-Service: Normalisierung, Budgetierung, PatchTarget-Ableitung.
- Tool-Adapter: dünner Adapter von `codecompass.plan_context` zu ToolResult.
- Worker: liest gezielte Ranges und erzeugt Patches; keine Orchestrierung
  anderer Worker.

Der Planner liefert keine unbounded Volltextantworten. Wenn CodeCompass nur
Treffer ohne LineRange liefert, werden sie nicht als harte `LocationRef`
verwendet; der Worker muss dann mit `repo.grep` oder anderen read-only Tools
weiter eingrenzen.

## Editor-Kontext

Der additive Vertrag `codecompass.editor_query.v1` unterstützt die Intents
`node_explanation`, `field_effect`, `io_contract`, `validation_issue`,
`runtime_error`, `dependency` und `safe_change`. Registry-Version, Node-Kind,
optionaler Feldpfad, Backend-Contract oder Symbole sowie Graphnachbarn bilden
den strukturellen Query-Kern. Freie Nutzersprache ist auf 600 Zeichen begrenzt
und darf nie das einzige Retrieval-Signal sein.

`preview` ist rein metadatenbasiert und löst weder Retrieval noch
Graph-Expansion, Repository-Content-Reads oder Modellaufrufe aus. `selected`
und `conversation` verwenden exakt die operativen Range-, Zeilen-, Evidence-
und Tokenbudgets des Visual-Process-Promptpfads. Treffer werden nach
Verification, Trust, Score absteigend, Pfad, Startzeile und Record-ID sortiert.
Das `codecompass_editor_context_bundle.v1` enthält für jede Auswahl oder
Verwerfung einen Budget-Trace; Content oder Vollrepository-Prompts gehören
nicht zu diesem Bundle.
