# Paralleler Multi-Tool-Use ohne generierte Tool-Call-JSON

**Track:** PDT · **Stand:** 2026-10-14
**SOLID-Bezug:** SRP (Audit, Contract, Adapter und Execution getrennt), OCP (mehrere Tool-Pläne als
Erweiterung des bestehenden `/v1/decision`-Pfads, kein Umbau), DIP (`argument_validator` als injizierter
Schema-Check), ISP (kleine Plan-Dataclasses statt eines Catch-all-Ports).

Ziel: mehrere unabhängige Tool-Aufrufe in **einem** Entscheidungs-Prefill bestimmen, typisierte Argumente
als geschlossene Kandidaten bewerten und die Tool-Calls deterministisch **außerhalb** des Modells
zusammenbauen. Confidence ist ein Vorschlag, nie eine Autorisierung; Hub-Policy, Approval und
Side-Effect-Freigabe bleiben unverändert.

## Teil 1 — Bestandsaudit (PDT-001)

Der Audit trennt bereits vorhandene Funktion von echten Lücken. Ergebnis: der Multi-Tool-*Plan* lässt sich
vollständig auf dem bestehenden `/v1/decision`-Pfad aufbauen; ein Fork-Eingriff ist nur für mehr als ein
offenes Freitextfeld nötig.

| Baustein | Ort | Rolle | Für PDT |
|---|---|---|---|
| Entscheidungs-Engine | `vendor/llama.cpp-vision-decision/tools/parallel-decision/decision-engine.{h,cpp}` | bewertet endliche Felder in einem Batched-Prefill, gemeinsamer KV-Trunk | vorhanden |
| `/v1/decision`-Schema | `README.md:103` (Schema), `README.md:477` (Tool calls) | Enum/Bool/Int/Number + **max. ein** offenes Feld (`README.md:391`, `when`-Bedingung) | vorhanden, mehr als ein offenes Feld fehlt |
| Single-Tool-Contract | `ananta_contracts/tool_decision.py` | ein `tool`-Enum + Argumentfelder, liest `ToolDecision` (CALL/RESPOND/ABSTAIN) | wiederverwendet (`fixed_values`) |
| Router-Adapter | `agent/services/tiny_router/parallel_decision.py` | HTTP-Runtime, Circuit-Breaker, `TinyActionModelAdapter` | unverändert |
| DPRV-Adapter | `agent/services/tiny_router/decision_provider_adapter.py` | Tool-Wahl über Jev/llama.cpp/LLM-Kaskade | unverändert |
| Kandidaten-only Router | `agent/services/tiny_router/service.py:69` | wählt **genau ein** `ToolCallCandidate`; keine Execution | Erweiterungspunkt für den Plan |
| Validator | `agent/services/tiny_router/validation.py:204` | lehnt `multiple_calls_not_supported` ab | Lücke: Multi-Call |
| Tool-Registry | `agent/services/ananta_tool_registry_service.py` | Kategorie/Risiko/Argumentschema je Tool | Schema-Quelle für `argument_validator` |
| Backend-Routing | `agent/services/tool_routing_service.py` | Backend- statt Tool-Auswahl | orthogonal |
| Tool-Execution | `agent/services/unified_tool_execution_service.py`, `agent/services/hub_tool_execution_adapter.py` | policy-gesteuerte Ausführung | Ziel von PDT-005 |

**Lücken (belegt):**

1. Es gibt keinen Vertrag für **mehrere** Tool-Calls mit unabhängigen `enabled`-Entscheidungen,
   Abhängigkeiten und per-Schritt-Abstain/Fehlern.
2. Der Validator (`validation.py:204`) verwirft Multi-Call-Ausgaben.
3. Der Fork generiert höchstens **ein** offenes Feld pro Request (`README.md:391`); mehrere freie Argumente
   können nicht parallel gescort/generiert werden.

**Go/No-Go Fork (PDT-006):** Für den Multi-Tool-Plan mit geschlossenen Argumentfeldern ist **kein**
Fork-Eingriff nötig — die Engine bewertet beliebig viele endliche Felder in einem Request. Ein
Fork-Patch lohnt nur, wenn empirisch mehr als ein offenes Freitextfeld pro Request nötig und schneller wird.
Bis dahin: `free_text_tools`/`free_text_required` → System-2-Fallback (PDT-003/004).

## Teil 2 — Backend-neutraler Plan-Contract (PDT-002)

`ananta_contracts/multi_tool_plan.py` (versioniert: `ananta.multi_tool_plan.v1`) ist Hub-*und*
Worker-importierbar (kein `agent.*`-Import, wie `tool_decision.py`). Er besitzt keinen Transport und keine
Policy.

- **Schema** — `build_multi_tool_plan_schema(tools, *, max_calls, open_field)`: pro Tool ein
  `<tool>__enabled`-Bool-Feld (unabhängig bewertet), je endlichem Pflichtargument ein geschlossenes Feld,
  `dependencies` markiert `independent`, `grouped:<tool>` und `conditional:<enabled>`.
- **Nicht scorebare Pflichtargumente** — freier String → `PlanTextField`; sonst (Objekt/Array/unklar) →
  `free_text_tools`, das Tool kann aktiviert werden, aber nur über System 2 aufgerufen werden.
- **Request** — `plan_request_body(schema, prompt, model_id=...)`: ein geteilter `contexts`-Eintrag,
  `mode="tree"`, `cache_context=False`.
- **Reader** — `read_multi_tool_plan(response, schema, *, min_confidence, max_calls, argument_validator)`:
  baut deterministisch in Schema-Reihenfolge; pro Schritt `call` / `skipped` / `abstain` / `error` mit Grund;
  `abstain` bei unsicherer Aktivierung, unsicherem Argument oder fehlendem Freitext; `argument_validator`
  (DIP) lässt den Hub die Argumente gegen die registrierten Schemas prüfen.
- **Ergebnis** — `MultiToolPlan.status` ∈ `plan` / `partial` / `respond` / `abstain` / `over_budget` / `error`;
  `router_payload()` liefert `tool_calls` in Schema-Reihenfolge; `plan_fingerprint()` ist ein deterministischer
  Digest der Calls (ohne Confidence/Disabled-Schritte); `plan_from_mapping()` ist der Audit-Round-Trip.
- **Budget** — mehr aktivierte Tools als `max_calls` werden nicht still gekürzt, sondern als `over_budget`
  markiert.

Registrierte Schemas werden nicht dupliziert: der Contract validiert geschlossene Argumente durch die
endlichen Wertemengen (`fixed_values`) und überlässt die strukturelle Prüfung dem injizierten
`argument_validator`.

**Tests (headless, ohne Netz/Modell):** `tests/decision/test_multi_tool_plan.py` (16 Fälle: Schema-Aufbau,
Request-Form, unabhängige Doppel-Auswahl, Disabled/Abstain/Partial, unbekannter Wert, Freitext-Fallback,
Open-Field-Generierung + Truncation, `over_budget`, `argument_validator`-Fehler, Feld-Mismatch,
Mapping-Round-Trip).

## Teil 3 — Plan-Route im Tiny-Router (PDT-003)

Additiv, ohne den Single-Candidate-Pfad zu verändern:

- `agent/services/tiny_router/multi_tool_adapter.py` — `MultiToolPlanAdapter` über
  `HttpParallelDecisionRuntime`/`CircuitBreaker`; baut Schema und Request über den Contract und liest die
  Antwort mit `read_multi_tool_plan`. `registry_argument_validator(tools)` prüft die Argumente gegen die
  exakt autorisierten Schemas (`validation.allowed_argument_schemas`).
- `TinyToolRouterService.plan(...)` (neu) — dieselben Scope-/Risk-/Deadline-Gates wie `route`; aktiv nur mit
  `multi_tool_plan: true` (Default aus). Ein negatives `<tool>__enabled` erzeugt nie einen Call; ohne
  aufrufbares Tool → `escalate` (System 2). Der Ein-Kandidaten-`route()`-Pfad bleibt unberührt.
- `MultiToolRoutingDecision` (neu in `types.py`, eigenes Schema `ananta.tiny_multi_tool_routing_decision.v1`)
  und `TinyRouterObserver.record_plan` (Telemetrie ohne Argumente, low-cardinality).

**Tests:** `tests/services/tiny_router/test_multi_tool_routing.py` (12 Fälle: Flag aus, additiver
`route()` unverändert, zwei unabhängige Tools in einem Request, negatives `enabled`, Shadow, Freitext-Abstain,
kein Tool/Endpunktfehler, Risk-Filter, Context-Isolation, Registry-Validator).

## Teil 4 — Abhängigkeiten und freie Argumente (PDT-004)

`ananta_contracts/tool_plan_graph.py` enthält Hub-konfigurierte `ToolDependency`, `ResultReference`
(JSON-Pointer) und `ResultCondition`. `dependency_waves` validiert unbekannte Knoten, doppelte Tool-IDs,
Selbstabhängigkeiten und Zyklen und liefert stabile topologische Ebenen. Der Hub dispatcht nur innerhalb
einer unabhängigen Ebene parallel. Fehlgeschlagene Eltern sperren Nachfolger. Bedingungen und aus
Elternergebnissen gebundene Argumente werden erst nach deren Abschluss aufgelöst und erneut gegen das
registrierte Schema validiert. Modelltext ist keine Quelle dieser Abhängigkeiten.

Die schnelle `plan()`-Route eskaliert bei Hub-konfigurierten `tool_dependencies` vor dem Scoring an System 2.
Bedingte JSON-Schemas (`if`/`then`, `allOf`, `dependentRequired`, `dependentSchemas`) werden nicht isoliert
als geschlossene Argumente geraten; nach Auflösung prüft der Validator ihre Bedingungen. Mehrere freie
Pflichtargumente bleiben System-2-Aufgabe. Ein einziges optional aktiviertes Open Field nutzt das bestehende
`when`; ein vom Backend übersprungenes Open Field beeinträchtigt andere Calls nicht.

## Teil 5 — Hub-Ausführung (PDT-005)

`HubMultiToolExecutionService` trennt DAG-Scheduling, Policy, Runtime-Gateway und Persistenz über kleine
Ports (`PlanGateway`, `PlanReceipts`, `PlanAudit`). `HubMultiToolPipeline` bindet die neue Route an
`TaskExecutionService._try_hub_direct_execution`. Der klassische Einzeltool-Pfad bleibt verfügbar.

- Scope kommt aus dem Hub-Task: Tenant, Projekt, Task, expliziter Workspace und die Schnittmenge aus
  Task-Toolrechten und `hub_direct_execution.allowed_tools`. Fehlende Bindungen aktivieren die Route nicht.
- Organization- und Recovery-Tasks bleiben auf ihren vorhandenen signierten Assignment-/Lease-Pfaden.
- Task-Scope und Terminalzustand werden nach Inferenz und vor Dispatch erneut geprüft.
- Nur registrierte `read`-Tools dürfen parallel laufen. Schreibende, administrative und irreversible Calls
  gehen zurück in den bestehenden Einzelcall-/Approval-Pfad. Confidence erteilt keine Freigabe.
- Jeder Call besteht erneut `AnantaToolPolicyService` und `HubToolExecutionAdapter`; tatsächlich ausgeführt
  wird über dessen vorhandenen `WorkerRuntimeExecutionAdapter`. Neue Worker-Orchestrierung entsteht nicht.
- Konfiguration wird pro Call kopiert; ContextVars und Flask-App-Kontext werden getrennt gebunden.
  Zugangsdaten kommen ausschließlich aus dem vorhandenen Hub-/Runtime-Pfad, nicht aus Modellargumenten.
- Maximal vier Dispatches je Hub-Service gleichzeitig, standardmäßig vier Calls pro Plan, begrenzte
  Ergebnisgrößen und eine gemeinsame Deadline. Ein Lauf endet headless mit einem strukturierten Ergebnis.
- Eine dauerhafte Admission pro Tenant/Projekt/Task begrenzt die Aufrufrate. Exakter semantischer Replay
  liefert das gespeicherte Ergebnis, andere Calls unter demselben Task werden blockiert. Confidence ist
  nicht Teil der Call-Identität. Neue beabsichtigte Ausführung benötigt einen neuen Hub-Task.
- `tool_plan_receipts` und `tool_plan_events` liegen in der gemeinsamen Hub-Datenbank. Migration:
  `e0a2c4d6f8b1_add_tool_plan_receipts`. Audit enthält Scope, Tool, Status und Digests, keine Argumentinhalte.
  Audit-/Receipt-Fehler blockieren. Nach Prozessabbruch bleiben laufende Receipts unsicher und werden
  niemals automatisch erneut ausgeführt.
- Ein Timeout widerruft die Annahme weiterer Ergebnisse. Bereits laufende Runtime-Aufrufe behalten ihren
  eigenen Timeout; ihr späteres Ergebnis kann keine erfolgreiche Planantwort mehr erzeugen. Es wird kein
  Thread-Kill oder bestätigter Abbruch vorgetäuscht.

**Bewahrte Architektur-Schuld:** Der bestehende Runtime-Adapter bietet auch einen lokalen Prozess-Backend
an. Diese Implementierung wird hier wiederverwendet, nicht erweitert; konfigurierte nicht verfügbare
Docker-/Remote-Backends dürfen weiterhin nicht still lokal ausweichen. Für eine streng containergebundene
Installation ist deren expliziter Runtime-Backend maßgeblich. Dies ist eine bestehende DIP-/Deployment-
Einschränkung des Adapters, keine neue Ausführungsimplementierung.

**SOLID-Prüfung:** Contract/Graph besitzen keine Hub-Imports; Transport bleibt im vorhandenen Adapter;
Scheduling bleibt im Hub-Service; Database-Admission und Audit bleiben im Repository. Kandidatenprüfung
und Nach-Inferenz-Abbruch/Deadline werden von Einzeltool- und Planroute gemeinsam verwendet. Der neue
Task-Hook delegiert an eine eigene Pipeline statt Geschäftslogik in den ohnehin großen Task-Service
aufzunehmen. Dessen vorhandene SRP-Schuld bleibt bestehen.

## Teil 6 — Fork-Entscheidung und Benchmark (PDT-006/007)

**PDT-006: No-Op.** Der vorhandene Fork bewertet bis zu 32 geschlossene Felder und unterstützt ein
bedingtes Open Field. Mehrere freie Argumente oder Ergebnisabhängigkeiten werden korrekt eskaliert;
ein zusätzliches `/v1/decision/tools` oder parallele freie Generation ist dafür nicht notwendig.
Der reale CPU-Vergleich bestätigt, dass diese Pfade auf demselben Server erreichbar sind. Keine neue
Fork-API, kein Fork-Commit und keine Submodule-Pointer-Änderung.

`scripts/multi_tool_benchmark.py` vergleicht Direct Decision, natives autoregressives Function Calling
und hybriden Fallback auf **einem lokalen Server und einem GGUF**. Der Operator bindet den Server an
`--model`; alle Antworten müssen denselben Alias tragen. GGUF-Digest und Hardware-Metadaten werden
aufgezeichnet, aber nicht als Hub-registrierte Evidence ausgegeben. Modelle mit verschiedener Bedeutung
hinter demselben Alias dürfen nicht über einen Load Balancer gemischt werden.

Der deterministische synthetische Satz enthält 0/1/2/3 Calls, Bool/Enum/Integer, freie Strings,
Ergebnisabhängigkeit und Prompt-Injection. Klassisches Function Calling hat maximal vier Decode-Runden
und erhält synthetische Tool-Ergebnisse; es führt keine echten Tools aus. Beim abhängigen Fall muss der
zweite Call auf das erste Ergebnis warten. Rohdaten enthalten Calls, Fehler, False Calls, Gesamtzeit,
Decode-Runden, gemessene Streaming-TTFT und Backend-Timings/Usage. Decision hat keine generierte TTFT:
dieses Feld bleibt `null`. VRAM-Snapshots bleiben bei fehlender NVML ebenfalls `null`; Snapshots sind keine
Kernel-Peak-Messung. Summaries enthalten p50/p95, Exact-Plan-Accuracy und Raw-Confidence-ECE/Brier für die
akzeptierten, bewerteten Pläne. Abgelehnte Pläne bleiben sichtbar und gelten nicht als korrekte Antworten.

Versionierte technische Beobachtungen:

- `artifacts/domain/multi-tool-benchmark.cpu.v1.json`: TinyGemma, 42 Vergleichszeilen, ungeeignete
  Ausgaben/Truncation; kein belastbarer Latenz- oder Qualitätsvergleich.
- `artifacts/domain/multi-tool-benchmark.qwen3-cpu.v1.json`: Qwen3-VL-2B Q8, 21 Zeilen, identisches Modell
  und CPU-Server. Direct 2/7 korrekt, klassisch und hybrid jeweils 5/7. Drei Calls und freie Argumente
  zeigen weiterhin Qualitätslücken. Die kurze Stichprobe und unterschiedliche Coverage begründen
  **No-Go für aktive Einführung**, keine Speedup-Behauptung und keine Kalibrierungsfreigabe.
- Der zwingende RTX-5060-Ti-16-GB-Lauf bleibt ausstehend: NVML ist nicht verfügbar, CUDA `cuInit`
  liefert Fehler 100 und meldet null Geräte. `--require-gpu` schreibt dafür ein begrenztes
  `blocked/gpu_unavailable`-Resultat statt einen CPU-Lauf als GPU-Nachweis zu deklarieren.

Beispiel für den späteren GPU-Lauf (gleicher Server für alle drei Routen):

```sh
python scripts/multi_tool_benchmark.py --url http://127.0.0.1:8096 \
  --model multi-tool-benchmark --gguf /models/model.gguf --repeats 5 \
  --require-gpu --output artifacts/domain/multi-tool-benchmark.cuda.v1.json
```

## Teil 7 — Bedienung, Tests und Rollout (PDT-008)

Default bleibt aus. Hub-Konfiguration zur Evaluation:

```json
{
  "hub_direct_execution": {
    "enabled": true,
    "allowed_tools": ["git.status", "git.diff_readonly"],
    "multi_tool_plan": {
      "multi_tool_plan": true,
      "mode": "shadow",
      "profile_order": ["bonsai2-27b-multi-tool-plan"],
      "max_plan_calls": 4,
      "execution_timeout_seconds": 30
    }
  }
}
```

Der Task benötigt passende Hub-Toolrechte, Tenant/Projekt und `workspace_dir`.
`ANANTA_PARALLEL_DECISION_URL` konfiguriert den vorhandenen Endpoint. Das neue Katalogprofil ist
**unkalibriert, Evaluation, Default aus**; die Single-Tool-Kalibrierung wird nicht auf Multi-Tool übertragen.
Shadow führt keine vorgeschlagenen Calls aus. `mode=disabled`, `multi_tool_plan=false` oder der
Kill-Switch schalten den Planpfad ab. Erst passende Hardware-/Holdout-Auswertung kann eine spätere
aktive Einführung begründen; diese Änderung aktiviert sie nicht.

Tests decken Contract-Kollisionen/Budgets, Response-Schemata, Shadow, Confidence, Cancellation,
Abhängigkeiten, Bedingungen, Tool-/Tenant-Rechte, parallele Dispatches, Deadline/unsichere Ergebnisse,
Audit-Ausfall, persistente Replays, Migration/Rollback und Task-Integration ab. Die CPU-CI-Workflow-Datei
nimmt die neuen Tests auf. Ein kompletter grüner Repository-Gate ist derzeit zusätzlich durch 18
vorbestehende, unveränderte Architektur-Importverstöße außerhalb dieses Tracks begrenzt.

Die Vision-Pilot-Korrektur verwendet `document-ui-pilot-v2`: eigenständige Layouts werden vor Varianten
auf Splits verteilt; Tests prüfen zusätzlich Bild-Digest-Disjunktheit. Die bestehenden v1-CPU-/CUDA-
Berichte sind ausdrücklich `evaluation_validity=invalidated`; ihre historischen Zahlen werden nicht
umgeschrieben und dürfen keine Rolloutentscheidung begründen. Ein neuer v2-Pilot muss separat laufen.

## Verwandt

- `docs/decision-providers.md`, `docs/tiny-tool-router-architecture.md`, `docs/jev-llamacpp-decision-mode.md`
- `todos/active/todo.parallel-decision-multi-tool-use.json`
