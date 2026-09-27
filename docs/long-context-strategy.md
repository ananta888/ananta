# Long context: fit check and never-silent truncation

**Track:** LCTX (`todos/active/todo.long-context-strategy.json`) · **Stand:** 2026-09-27, Meilensteine M0–M2, M3 teilweise

Ananta arbeitet mit **32k Token pro Anfrage** (`ANANTA_CONTEXT_TOKENS`, Standard 32768; siehe
`docs/jev-llamacpp-decision-mode.md`, Abschnitt Standardmodell). Der Hub entscheidet, wie eine zu große
Aufgabe zu behandeln ist (M2), und kann sie nacheinander oder parallel über Hub-Tasks vollständig verarbeiten
(M3, Modus `active`). Wo noch gekürzt wird, geschieht es nie mehr still. Verdichten und gezielt nachladen als
handelnde Strategien folgen.

## Fenster anwenden (M0)

| Stelle | Verhalten |
|---|---|
| `generate_text` | `llm_config.context_limit` für seinen Provider, sonst 32k für lokale Runtimes (Ollama, LM Studio, llamacpp); Cloud nur mit explizitem Limit |
| llamacpp-/OpenAI-Pfad | kürzt den Verlauf auf das Fenster (System-Prompt und neueste Nachricht bleiben) |
| CLI-Backends | `MAX_PROMPT_TOKENS`; sonst sgpt = Fenster, opencode = 128k |
| Worker-Tool-Loop | `max_total_tool_result_chars` (Standard: halbes Fenster); ältere Ergebnisse werden markiert verdichtet |

## Passt-Prüfung und Kürzungsprotokoll (M1)

`agent/context_window.py`:

- `check_fit(prompt=..., messages=..., window_tokens=..., output_reserve_tokens=...)` → `ContextFit`
  (Schätzung ~4 Zeichen/Token, dieselbe wie beim Kürzen). `generate_text` prüft vor jedem Aufruf und zählt
  erwartete Überläufe (`context_overflow_expected_total{site}`).
- `record_truncation(site, kind, before_tokens=..., after_tokens=..., dropped_items=...)`: jede Kürzung wird
  geloggt (`ananta.context_window`, WARNING), gezählt (`context_truncation_total{site,kind}`) und für den
  laufenden Aufruf gesammelt (`truncation_scope()`).

Aufgezeichnete Stellen:

| site | kind | was |
|---|---|---|
| `llm.trim_messages` | trim_messages | Verlaufskürzung aller Strategien |
| `llm.lmstudio_completion` | char_cut | Completion-Prompt von vorn abgeschnitten |
| `tool_loop.results` | condense | ältere Tool-Ergebnisse verdichtet |
| `planning.context` | char_cut | Planungskontext über `context_max_chars` |
| `recovery.context` / `recovery.goal` | char_cut | Recovery-Kontext (8000) / Ziel (1600 Zeichen) |
| `context_bundle` | drop_items | Chunks über `total_budget_tokens` |
| `context_compression` | condense | Kompression des RAG-Kontexts (wenn eingeschaltet) |

**Sichtbar am Ergebnis:** `generate_text` hängt `metadata.context_truncation` an (`truncated`, `events`,
`lost_tokens`, `dropped_items`) und legt die Ereignisse in `g.llm_context_truncations`; `/llm/generate`
liefert sie als `context_truncation` in den Metadaten.

## Bereinigt (M1)

- **Kontext-Bündler:** `total_budget_tokens` wird durchgesetzt (Chunks in Reihenfolge bis zum Budget, mindestens
  einer), `context_policy.budget_dropped_chunks` nennt die weggefallenen.
- **Kontext-Kompression:** die `CONTEXT_COMPRESSION_*`-Einstellungen wirken jetzt
  (`agent/services/context_compression/settings_config.py`); vorher las der Orchestrator ein nie vorhandenes
  `settings.global_config`. Standard weiterhin aus.
- **Bewusst behalten:** `ContextBudgetPolicyService` wählt je Chat-Absicht die erlaubten Kontextquellen,
  `PreModelContextOrchestrator` rankt Kandidaten – beides andere Fragen als „was tun, wenn es zu groß ist“;
  der Orchestrator ist ein Baustein für die Strategie „gezielt nachladen“ (LCTX-006).

## Entscheidungspunkt im Hub (M2)

`agent/services/context_strategy_service.py`, im Propose-Ablauf des Hubs (`_task_scoped_propose_orch`):
passt der Task-Kontext (Prompt, Recherche-Kontext, Beschreibung) nicht ins Fenster, entscheidet der Hub:

| Lage | Strategie |
|---|---|
| passt | `fit` |
| bis 1,5× Budget, oder Verlauf | `compact` |
| großer Bestand / Frage an das Material (nicht geordnet) | `retrieve` |
| unabhängige Teile | `map_reduce` (Stückbudget = 60 % des Budgets, Parallelität ≤ 4) |
| geordnetes Material | `sequential` |
| ≥ 40× Budget | `escalate` |
| unklare Form | Decision-Provider-Bereich `context_strategy` (falls an), sonst `sequential` (verarbeitet alles) |

Die Art der Eingabe kann ein Task mitbringen (`context_input_kind`: conversation / corpus / parts / ordered);
Analyse-, Recherche- und Review-Tasks gelten als Fragen an das Material. Config `context_strategy`
(`mode` off / **shadow** / active, `compact_max_ratio`, `escalate_min_ratio`, `chunk_fill`, `max_parallel`,
`ask_decision_provider`). Aufgezeichnet als Produkt-Event `context_strategy_decided` und Metrik
`context_strategy_decisions_total{strategy,decided_by}`. Im Modus `shadow` (Standard) wird nur entschieden und aufgezeichnet;
im Modus `active` handeln `sequential` und `map_reduce` (siehe unten).

## Nacheinander und parallel (M3, LCTX-007/008)

Mit `context_strategy.mode = active` zerlegt der Hub einen zu großen Task, **bevor** er an einen Worker geht
(Autopilot-Dispatcher), wenn die Entscheidung `sequential` oder `map_reduce` lautet
(`agent/services/long_context_coordinator.py`):

- **Stücke:** `split_ordered` (geordnetes Material an Absatz-/Zeilen-/Wortgrenzen) bzw. `pack_parts`
  (unabhängige Teile; ein zu großes Teil wird selbst geteilt, seine Herkunft bleibt am Stück). Nichts fällt weg.
- **Schritte** (`agent/services/long_context_plan.py`) werden normale Hub-Tasks (`ingest_task`), verknüpft über
  `source_task_id` (nicht `parent_task_id` – sonst warteten die Schritte auf den Task, der auf sie wartet) und
  `depends_on`; Marker `status_reason_details.long_context`.
  - `sequential`: Teil 1 → Teil 2 → … → Ergebnis. Jeder Schritt bekommt den **Zwischenstand** des vorigen und
    liefert ihn aktualisiert zurück (`## Zwischenstand`).
  - `map_reduce`: Teile unabhängig (`## Teilergebnis`), höchstens `max_parallel` gleichzeitig (Wellen über
    `depends_on`), dann Zusammenführung. Ist die Zusammenführung selbst zu groß, wird sie wieder zerlegt
    (nacheinander, höchstens 2 Ebenen).
- **Eingaben** der Vorgänger setzt der Hub in dem Moment ein, in dem ein Schritt freigegeben wird
  (`reconcile_dependencies`); vorher steht dort `{DEPENDENCY_OUTPUTS}`.
- **Abschluss:** ist der letzte Schritt fertig, übernimmt der ursprüngliche Task dessen Ergebnis und ist
  erledigt (`long_context_completed`); scheitert ein Schritt, scheitern die abhängigen und der ursprüngliche
  Task über die normalen Abhängigkeitsregeln.
- **Eingabe beschreiben** (optional, in `worker_execution_context`): `context_input_kind`
  (conversation / corpus / parts / ordered), `context_parts` (`[{id, text}]`, unabhängige Teile),
  `context_goal` (die eigentliche Aufgabe, wenn die Beschreibung vor allem Material ist).
- Worker sehen nur ihren Schritt; keine Worker-zu-Worker-Weitergabe.

## Der Worker bleibt pro Iteration im Fenster (LCTX-012)

Der ananta-worker bekommt Auftrag und Kontext vom Hub; Hub-Kontext, Recherche und Aufgabe liegen als Dateien
im Workspace (`.ananta/…`, `rag_helper/…`) und werden bei Bedarf gelesen – der Prompt verweist nur darauf. Beim
Abarbeiten wachsen Teile des Prompts: Tool-Ergebnisse (Tool-Loop), Arbeitsfortschritt (Batch-Loop),
Feedback-Evidence (Mutations-Loop). `agent/cli_backends/context_budget.py` gibt ihnen genau den Platz, den das
Fenster lässt:

    verfügbar = Fenster (32k) − Antwortreserve (2k) − feste Teile (Auftrag, Anweisungen, aktueller Stapel)

Die neuesten Einträge bleiben vollständig, ältere werden verdichtet (Überschrift und Anfang), die ältesten
mit Vermerk ausgelassen – protokolliert (`tool_loop.results`, `batch_loop.progress`, `mutation_loop.evidence`).
Der Batch-Loop schneidet den Fortschritt nicht mehr hart auf 6000 Zeichen. Das `sgpt`-Budget-Gate bleibt das
letzte Sicherheitsnetz. Braucht eine Aufgabe alles Material zugleich, zerlegt der Hub sie vorher (oben); jeder
Schritt läuft dann wieder unter diesem Budget.

## Noch offen (M3–M4)

Verdichten (LCTX-005) und gezielt nachladen (LCTX-006) als handelnde Strategien,
Überlauf zur Laufzeit neu anstoßen statt nur protokollieren, Ersatz der harten Zeichenschnitte, Benchmark.
