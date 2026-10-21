# Worker-Ausführung, Werkzeuge und Backends: Ist, Ziel und Lücken

Scope: wie der Hub Arbeit an Workers übergibt, welche Werkzeuge dort greifen,
wie `opencode` und `ananta-worker` denselben Werkzeugsatz benutzen und wo die
Grenzen (Git, Publication, Evidence) liegen. Entstanden aus einer Operator-Idee:

1. `opencode` (TUI bzw. `serve`) bekommt Tool-Zugriff — read und write — auf die
   vom Hub gelieferte Git-Repräsentation der Worker-Dateien, nachvollziehbar
   auch von einer zweiten `opencode`-TUI.
2. `ananta-worker` braucht dieselben direkten Zugriffe (git, read, write).
3. CodeCompass dient beiden Seiten: als Befüller der Prompts **und** als
   MCP-Server.

Vorrang dieses Dokuments: **den Bestand genau abbilden, damit nichts doppelt
gebaut wird.** Jede Lücke nennt ihre Fundstelle.

---

## 1. Was bereits existiert (nicht nachbauen)

### 1.1 Eine zentrale Werkzeug-Registry — für beide Pfade

| Baustein | Fundstelle |
|---|---|
| Werkzeug-Specs (name, description, inputSchema, category) | `agent/services/ananta_tool_registry_service.py:700` — `list_tools(category=…)` |
| Werkzeug-Policy (eine Instanz für beide Pfade) | `ananta_tool_policy_service.evaluate(...)` — im MCP wie im Loop |
| Tool-Aliase (`resolve_alias`) | `ananta_tool_registry_service.py:706` |

Der MCP-Server für `opencode` **liest dieselben Specs** aus der Registry
(`opencode_codecompass_mcp.py:24-31`: `registry.list_tools(category="read_only")`,
gefiltert auf `codecompass.*`). Der `ananta-worker`-Loop fragt dieselben Specs
über Protokoll-Requests ab (`{"kind":"tool_request","tool_name":"codecompass.plan_context",…}`).

> **Konsequenz für die Architektur:** Werkzeugdefinitionen und Policy sind
> *nicht* doppelt vorhanden. Was doppelt ist, sind ausschließlich zwei
> **Transporte** (MCP-RPC vs. Protokoll-Antwort im Prompt). Ein Eingriff, der
> „einheitliche Tools" erzwingt, muss am Transport ansetzen — nie an den Specs.

### 1.2 read- und write-Werkzeuge im Worker

| Zugriff | Werkzeug |
|---|---|
| read | `repo.read_file_range`, `repo.list_files`, `repo.grep`, `git.status`, `git.diff_readonly` |
| write | `repo.write_file`, `repo.apply_patch`, sowie die Aktionen `workspace_write` und `patch_request` des Mutation-Loops |
| Kontext | `codecompass.plan_context` (materialisiert Bereiche), `codecompass.search*`, `codecompass.get_file_context`, … |

Die `workspace_write`/`patch_request`-Aktionen sind zusätzlich durch die
Analysis-Gates gesperrt (Quelle muss zuerst beobachtet sein) — siehe
`docs/self-development-quality-gates.md`.

### 1.3 Git im Worker: Commit und Push sind bereits Werkzeuge

`agent/tools_git.py` liefert `git.status`, `git.commit` und `git.push` mit
regelnbasierten Sperrungen, belegt in `tests/workspace/test_workspace_git_tools.py`:

- `git.commit` arbeitet im Workspace-CWD, lehnt blockierte Messages und `wip`
  ab, akzeptiert gültige scoped Messages (`test_git_commit_*`, Zeilen 52-79)
- `--no-verify` wird **nie** gesetzt (`test_no_verify_flag_never_used`, Zeile 79)
- `git.push` ohne Remote liefert einen Fehler statt eines Erfolgs (Zeile 93)
- Die Klassifizierung als Schreibzugriff ist bereits vorhanden:
  `agent/tool_guardrails.py:32` — `"git_commit": "write"`, und
  `agent/config_defaults_quality.py:90-91` führt `git.push`/`git.commit`

Damit ist die von der Idee geforderte „git- und read-write-Fähigkeit im
Worker" **größtenteils vorhanden** — was fehlt, ist nicht die Funktion,
sondern die Frage, ob und unter welcher Policy das Modell sie benutzen darf
(siehe Lücke L4).

### 1.4 Git-Representation, die der Hub liefert

`agent/services/self_development/workspace.py`:

- `export_checkout(checkout, destination)` (Zeile 42): kopiert **nur getrackte
  Dateien** (`git ls-files`), ausdrücklich „No Git config, hooks or credentials"
- `export_revision(...)` (Zeile 62): `git archive` einer konkreten Revision,
  traversal-guarded — der Review sieht die admittierten Bytes, nicht den
  Working-Tree
- `write_scope_manifest(...)`, `validate_source_files(...)`, `seal_changes(...)`
  (Zeile 106/124/156): Scope, Quellprüfungen und das Siegel der Change-Set-Grenze
- Publikation ausschließlich Hub-seitig: `seal` → `git.stage` → `git.commit` →
  `git.push` (`agent/services/self_development/publisher.py`)

### 1.5 `opencode`: TUI, `serve`, Sessions

- Modi über Env (additiv gesetzt, `agent/config_defaults_env.py:141/144`):
  `ANANTA_OPENCODE_EXECUTION_MODE` ∈ `backend | live_terminal | interactive_terminal`,
  `ANANTA_OPENCODE_INTERACTIVE_LAUNCH_MODE` ∈ `run | tui`
- persistente Server: `agent/services/opencode_runtime_service.py` startet
  `opencode serve --hostname 127.0.0.1 --port <frei>` (Zeile 152-164) und
  verwaltet Server je Session-Scope (`_ensure_server`, Resume über
  `native_session_id`)
- CodeCompass als MCP: `agent/cli_backends/opencode_codecompass_mcp.py:17` —
  baut einen MCP-Endpoint inklusive **Task-Capability-Binding** und
  **Policy-Prüfung** und wird über `OPENCODE_CONFIG_CONTENT["mcp"]["ananta_codecompass"]`
  in den `opencode`-Aufruf injiziert

### 1.6 Strukturierte Mutation-Antworten (in dieser Session ergänzt)

- `agent/cli_backends/workspace_mutation/structured_runner.py` — schickt jede
  Loop-Antwort mit `response_format: json_schema` an den lokalen Runner; das
  Server-Sampling kann dann kein unvalides JSON erzeugen
- Auswahl am bestehenden Seam: `_orchestrator.resolve_mutation_llm_runner(cfg, llm_runner)`
  (injizierter Runner > strukturierter Runner > CLI), **default off**
- Zuschaltung je Container: `ANANTA_WORKSPACE_MUTATION_STRUCTURED_OUTPUT`
  (Code-Default `false`, dieser Stack setzt `1`)
- Schema (`MUTATION_JSON_SCHEMA`) als `anyOf` je `kind` mit `required`-Pflicht
  pro Variante in `strict: true` — es erzwingt genau die Felder, die
  `parse_mutation_output` verlangt (`files` bei `workspace_write`,
  `target_path` bei `patch_request`, `tool_name` bei `tool_request`,
  `sources`+Begründungen bei `code_analysis`)
- Tests: `tests/self_development/test_structured_mutation_runner.py`

---

## 2. Lücken (Gap-Analyse)

### L1 — Blocker: die Backend-Wahl priorisiert `task_kind_backend`

**Symptom (belegbar):** In `opencode`-α erzeugt derselbe Auftrag einen
`mutation-report.json` (also den `ananta-worker`-Loop), obwohl
`SGPT_EXECUTION_BACKEND=opencode` **und** `sgpt_routing.default_backend=opencode`
gesetzt sind. Beide Worker loggen identisch:

```
cli_dispatch requested=ananta-worker role=worker
```

**Ursachenkette:**

1. `build_default_agent_config()` liefert
   `sgpt_routing.task_kind_backend = {coding: ananta-worker, analysis: …, research: deerflow}`
   (`agent/config_defaults.py:296`); zusätzlich existiert eine DB-Row `sgpt_routing`
   mit demselben Block.
2. `runtime_policy.resolve_cli_backend` prüft **zuerst** `kind_map[task_kind]`
   (`agent/runtime_policy.py:80-83`) und erst danach `default_backend`
   (Zeile 85).
3. Ein Override, der nur `default_backend` setzt, bleibt deshalb wirkungslos,
   solange `task_kind_backend` den Task-Kind kennt. Ein Self-Dev-Task läuft mit
   `task_kind: "coding"` (`retries.py`).

**Prüfbeweis (in beiden Containern ausgeführt):**

```
nach ENV: default_backend = opencode | task_kind_backend = {'coding': 'ananta-worker', …}
resolve(task_kind='coding') -> backend=ananta-worker | reason=task_kind_policy:coding->ananta-worker
```

**Nicht** die Ursache (zuvor falsch vermutet): die Durchreichung des Wertes an
den Runner funktioniert korrekt — `_invoke_primary_cli` schreibt
`"backend": run.effective_backend` in die `cli_kwargs`
(`agent/services/_task_scoped_propose_single.py:300`), und
`invoke_cli_runner` filtert nur nach der Signatur
(`_task_scoped_cli_invocation.py:26-56`).

**Fix (reine Konfiguration, kein Code):** die bestehende Env-Variable
`ANANTA_SGPT_ROUTING_TASK_KIND_BACKEND` (JSON-Map, bereits von
`_apply_sgpt_routing_env` ausgewertet) je Worker setzen. α nennt dort
`opencode` für `coding`/`analysis`/`doc`/`ops` und behält `research: deerflow`;
β braucht nichts, da der Default passt.

Regression: ein Test, dass `resolve_cli_backend(task_kind="coding")` dem
gesetzten `task_kind_backend`-Eintrag folgt und `default_backend` nur greift,
wenn der Kind nicht gemappt ist.


### L2 — MCP-Export ist read-only

`opencode_codecompass_mcp.py:26` exportiert ausschließlich
`category="read_only"`-Specs. `repo.write_file`, `repo.apply_patch` und
`workspace_write` erreichen `opencode` damit **nicht** über MCP (opencode
bearbeitet Dateien derweil mit eigenen Edit-Werkzeugen im Workspace).

**Entscheidung nötig:** write-Specs über MCP exportieren? Das betrifft die
Approval-Policy (`tool_guardrails` klassifiziert Schreibzugriffe als `write`),
die dann auch für den MCP-Endpunkt gelten muss — die Policy-Instanz ist
glücklicherweise bereits dieselbe.

### L3 — `opencode serve` ist nur im Container erreichbar

`--hostname 127.0.0.1` und `_pick_free_port()` (`opencode_runtime_service.py:30/158`)
binden strikt lokal. „Von einer zweiten opencode-TUI nachvollziehbar verbindbar"
setzt voraus: `0.0.0.0` oder eine gezielte Port-Freigabe, einen bekannten
Session-/Server-Scope zwischen Containern und (für externe TUIs) einen Weg
über Container-Netz oder Port-Mapping.

**Additiver Einstieg:** Env für den Hostnamen (Default bleibt `127.0.0.1`),
Port-Freigabe nur auf expliziten Wunsch.

### L4 — Git-Publication: Worker-Fähigkeit vs. Hub-Grenze

Der Worker besitzt `git.commit`/`git.push` als Werkzeuge (§1.3), der
Self-Dev-Export enthält jedoch **kein `.git`** (§1.4), und Publikation ist
Hub-Sache: `seal_changes` → Publisher. Genau das sichert die Trennung aus
`AGENTS.md` (Hub ist Eigentümer von Evidence, Publication und
`SRC_*`/`RUN_*`-Identitäten).

**Mögliche Ausbaustufen, bewusst in dieser Reihenfolge:**

1. (heutiger Stand) Worker: `git.status`, `git.diff_readonly`, write im
   Workspace; Hub: Commit/Push — **kein Eingriff nötig**
2. Worker bekommt eine echte `.git`-Ansicht (Export mit Git-Metadaten oder
   `git init` + Commit lokal), bleibt aber **read-only gegen Remote**
3. Worker darf committen, Hub bleibt der einzige Pusher (Commit-Sha wird als
   Artefakt/Receipt an den Hub gemeldet, Publikation weiterhin Hub-seitig)

Stufe 3 berührt die Evidence-Kette und gehört deshalb in eine eigene
Entscheidung mit Test- und Audit-Nachweis.

### L5 — Zwei Transporte für CodeCompass (kein Doppel-Code der Werkzeuge)

| Pfad | Transport |
|---|---|
| `opencode` | MCP-RPC (`mcp.ananta_codecompass`), Specs aus der Registry, Policy-Prüfung pro Call |
| `ananta-worker`-Loop | Protokoll-Tool-Requests im Prompt, dieselbe Registry und Policy |

Beide Pfade sind funktional äquivalent (gleiche Specs, gleiche Policy, gleiche
Capability-Binding). **Kein zweiter Code für Werkzeuge nötig.**

Optionaler Vereinheitlichungspunkt, falls gewünscht: einen Helfer extrahieren,
der die `codecompass.*`-Specs plus Policy-Einbindung aus der Registry baut und
sowohl an den MCP-Endpunkt als auch an den Protokollpfad liefert — die
bestehenden Implementierungen würden dann nur noch konsumieren (ein Helfer,
zwei Konsumenten, keine Kopie).

### L6 — `opencode`-Ergebnis erreicht die Hub-Evidence-Kette nicht

**Belegt im Vergleichslauf (identische Aufgabe `text_budget`, α `opencode` vs. β
`ananta-worker`):**

- α schreibt **beide deklarierten Dateien korrekt** (1.061 B + 3.058 B,
  `clamp_budget`/`truncation_marker` nach Spezifikation), die Suite im
  Workspace läuft grün — und der Läuf endet trotzdem mit
  `development_evidence_gate_failed:evidence_run_not_successful`.
- αs `.ananta/` enthält **weder** `last-final-answer.json` **noch** einen
  `mutation-report.json`.
- β liefert den Run-Nachweis (Mutation-Report, Analysis-Gates, Protokoll), löst
  die Aufgabe in denselben Iterationen aber **nicht** (kein `files`-Write
  durchgekommen).

**Kernsatz:** `opencode` liefert **Ergebnis ohne Nachweis**, `ananta-worker`
liefert **Nachweis ohne Ergebnis** — beide scheitern an derselben Evidence-Gate,
aus entgegengesetzten Richtungen.

Für `opencode` fehlt die Brücke, die der `ananta-worker`-Loop implizit baut:
eine vom Hub reservierte Run-Identität mit eingereichtem Ergebnis (final
answer, Datei-Digests, Verifikationsbefund) — dieselbe Kette, die
`AGENTS.md` unter „Hub Evidence Identity" beschreibt (`RUN_*` vor Ausführung
reservieren, Ergebnis danach einreichen).

**Fix-Richtung (Variante A — entschieden):** nicht `opencode` eine separate
Evidence-Brücke bauen, sondern den **gemeinsamen Struktur-Pfad** bespielen: alle
Coding-Worker laufen im selben Mutation-Loop (CodeCompass-Reads, Write-Policy,
Evidence-Report, `final_answer`-Protokoll) — nur das **Brain/CLI** wechselt.
Ein Submit-Pfad für einen nativen Sonderweg überlässt die Compliancelücken
(CodeCompass-Call, Verdict-Call) weiterhin dem Zufallsverhalten des Modells und
ist damit keine Dauerlösung.

Umgesetzt über:

- **Runner-Key:** `ANANTA_WORKSPACE_MUTATION_LLM_BACKEND=opencode` →
  `ananta_worker_workspace_mutation.llm_backend` →
  `resolve_mutation_llm_runner` wählt `run_opencode_mutation_llm`
  (`(rc, out, err)`-Kontrakt wie alle Runner; Präzedenz: injiziert > Brain >
  structured > CLI-Default).
- **Routing:** der per-Kind-Map-Wert bleibt der *Struktur*-Backend-Name
  (`ananta-worker` → Loop-Pfad); das Brain trägt der separate Key. αs Map ist
  parametrisiert: `ANANTA_WORKER_ALPHA_TASK_KIND_CODE` (Default
  `ananta-worker`, Wert `opencode` = nativer Pfad als Vergleichs-Flag),
  Brain-Default in Compose: `ANANTA_WORKER_ALPHA_MUTATION_BRAIN=opencode`.
- **Verträge unverändert:** `codecompass_observed: Literal[True]`, Evidence-Gate,
  Review-Verdict-Vertrag — der Loop erzwingt sie für jedes Brain strukturell.

Bekannte Risiken der Variante: (1) opencode besitzt eigene Schreib-Werkzeuge und
könnte direkt schreiben, obwohl der Loop die Schreibvorgänge über seine Handler
erwartet — Beobachtung im Live-Nachweis, andernfalls prompt-seitige Regel für
den Loop-Modus; (2) `structured_output` gilt nur für Nicht-`opencode`-Brains
(bevorzugtes Brain entscheidet, wie die Antwort erzwungen wird).

**Phasen:**
1. ~~Runner-Adapter + Config~~ — erledigt (`opencode_runner.py`,
   `resolve_mutation_llm_runner`, Env-Plumbing mit bounded Validierung,
   `tests/self_development/test_mutation_llm_backend.py`).
2. ~~α-Routing + Brain in Compose~~ — erledigt (parametrisierte per-Kind-Map +
   `ANANTA_WORKSPACE_MUTATION_LLM_BACKEND`).
3. **Live-Nachweis offen:** α-Loop-Lauf muss `pending_review → published`
   erreichen; danach der Vergleichslauf α(Loop+opencode-Brain) vs
   β(Loop+CLI-Brain) unter allen Gates.

---

## 3. Empfohlene Reihenfolge (additiv, ohne Doppel-Code)

1. ~~**L1 schließen**~~ — **erledigt**: `ANANTA_SGPT_ROUTING_TASK_KIND_BACKEND`
   je Worker gesetzt, geprüft mit `resolve(task_kind='coding') → opencode | ananta-worker`,
   Regression in `tests/self_development/test_backend_routing_priority.py`.
2. **Vergleich fahren** — läuft; erster Befund unter L6 (α löst ohne Nachweis,
   β liefert Nachweis ohne Ergebnis).
3. **L6 schließen (Variante A)** — Phasen 1+2 erledigt (Runner-Key + α-Routing);
   Phase 3 (Live-Nachweis `pending_review → published` mit opencode-Brain) läuft.
   Erst danach ist ein `opencode`-Lauf jenseits von `failed` möglich.
4. **L2 + L5** — MCP-Export um read/write-Specs für den **lokalen
   Worker-Clone** erweitern, Spec-/Binding-Teil in den gemeinsamen Helfer legen
   (Approval-Policy nachweisen; kein Zugriff auf Hub- oder Origin-Repo).
5. **L4 (Stufe 2)** — echte `.git`-Ansicht im Export (Remote bleibt leer,
   damit `git.push` sicher scheitert).
6. **L3 (Variante c)** — `opencode serve` von außen erreichbar **mit**
   Zugriffsschutz (z. B. Port nur an `127.0.0.1` des Hosts gebunden).

## 4. Offene Entscheidungen des Operators

1. **L1 umsetzen** (Voraussetzung für jeden gültigen Backend-Vergleich)?
2. **L2:** write-Werkzeuge über MCP an `opencode` — ja/nein, und welche
   Approval-Policy soll dort greifen?
3. **L3:** `opencode serve` vernetzen — welche Grenze (nur Container-Netz,
   oder auch von außen)?
4. **L4:** Git-Ausbaustufe 2 oder 3, oder heutigen Stand belassen?
5. **L5:** geteilten Spec-Helfer jetzt extrahieren oder bis zum nächsten
   Anlass aufschieben?
