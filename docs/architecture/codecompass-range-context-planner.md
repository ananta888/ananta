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
