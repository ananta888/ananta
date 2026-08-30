# Visual Process Assistant

## Ziel und Systemgrenze

Der Visual Process Assistant ergänzt den bestehenden Visual Process Editor um
registry-gesteuerte Formulare, deterministische Kontexthilfe und belegte
AI-Snake-Antworten. Er führt keinen zweiten Editor und keine zweite
Task-Kind-Registry ein. Der Hub bleibt Control Plane und alleiniger Eigentümer
von Kontext, Conversation, Task Queue, Evidence-Annahme, Patch-Governance und
Persistenz. Worker führen genau den vom Hub delegierten Retrieval- oder
Inferenzauftrag aus; sie erstellen keine Folgetasks und persistieren keine
Workflowmutation.

```text
Angular Editor
    │  authentifizierte Assistant-API
    ▼
Hub: Context-Snapshot ─► Retrieval-Task ─► Worker: CodeCompass + Scan
    │                           │
    │      geprüfte Evidence ◄──┘
    ▼
Hub: Prompt-Snapshot ──► Inferenz-Task ──► Worker: typisierte HelpResponse
    │
    ├─ Antwort + Evidence an Angular
    └─ optionaler Patch ─► Hub-Preview ─► Nutzerbestätigung ─► EditorCommand
```

Container teilen dabei keinen impliziten Dateisystemzustand. Task-Envelopes und
Worker-Ergebnisse sind die Transportgrenze. Der Worker akzeptiert nur ein vom
Hub ausgestelltes, gehashtes Envelope mit Deadline und authentifizierter
Task-ID. Das interne Ergebnis-Ende
`POST /api/visual-process/assistant/v1/worker-results/<task_id>` ist mit
Service-Authentifizierung geschützt und kein Browser-Endpunkt.

## Registry und Editor-Vertrag

`agent.visual_process.task_kind_registry` ist die Runtime-Source-of-Truth. Eine
NodeDefinition komponiert diese Runtime-Fakten mit Darstellung, Feldschema,
sicheren Defaults, Ressourcenreferenzen und Capability-Flags. Der Angular
Inspector rendert den versionierten Vertrag generisch. Kind-spezifische
Kompatibilitätsfelder dienen nur der verlustfreien Migration und werden nicht
zu einer zweiten Registry.

Die Angular-Fallback-Liste bleibt für einen Hub-Ausfall erhalten. Diese
bewusste Kompatibilitätsduplikation ist eine enge OCP/DIP-Ausnahme: Sie darf
keine eigene Runtime-Wahrheit begründen. Das reproduzierbare Baseline-Gate
verlangt daher vollständige ID-Parität und schlägt bei einseitigen Kinds,
unbekannten Adaptern oder Alias-Drift fehl:

```bash
python scripts/generate_visual_process_assistant_baseline.py --check
python -m pytest -q tests/test_visual_process_assistant_baseline.py
```

Das resultierende Inventar liegt in
`artifacts/domain/visual-process-assistant-baseline.json`. Es enthält keine
Zeitstempel, absoluten Pfade oder synthetischen Source-/Run-Kennungen.

## Versionen und Graphzustand

Die folgenden Werte haben getrennte Bedeutungen und dürfen nicht ineinander
umgedeutet werden:

| Wert | Bedeutung |
| --- | --- |
| `version` | bestehende fachliche/Legacy-Version des gespeicherten Graphen |
| `graph_schema_version` | Version der Graphstruktur |
| `node_registry_version` | Version des NodeDefinition-Katalogs |
| `definition_revision` | monotoner Persistenzstand für optimistisches Locking |
| `definition_hash` | Hash der gespeicherten, runtimefreien Definition |
| `draft_hash` | Hash des aktuellen Editorentwurfs |
| `runtime_snapshot_hash` | getrennte Referenz auf den flüchtigen Runtime-Overlay |

Speichern verwendet Compare-and-Swap mit erwarteter Revision und Basis-Hash.
Ein Konflikt liefert HTTP 409; er wird nicht durch stilles Überschreiben
repariert. Der Editor bietet stattdessen Reload oder Fork; ein konfliktbehafteter
Assistant-Patch kann über `POST /requests/<id>/patch-refresh` mit einem neuen,
immutablen Context des aktuellen Drafts erneut durch Retrieval, Inferenz und
Hub-Preview validiert werden. Der alte Request und sein Audit bleiben dabei
unverändert. Unbekannte Kinds und Felder bleiben im Kompatibilitätsmodus
verlustfrei erhalten. `run_state` wird aus der Definition herausprojiziert und
nur über den Runtime-Overlay dargestellt.

## Deterministischer EditorContext

Der Context wird UTF-8-kodiert, in Unicode NFC normalisiert und als kanonisches
JSON mit lexikografisch sortierten Objektschlüsseln serialisiert. Sortierregeln
für Steps, Edges, Ports, Issues und Evidence sind Teil des Vertrags. Fehlende
Felder bleiben ausgelassen, explizites `null` bleibt erhalten; NaN und Infinity
sind verboten. DOM-Referenzen, Mauskoordinaten, Animationen, Poll-Zähler,
Zeitstempel und zufällige UI-IDs sind ausgeschlossen.

Ein gültiger Kontext bindet mindestens:

- Repository-Revision und CodeCompass-Manifest-Hash,
- Source-Allowlist- und Prompt-Version,
- Graphschema-, Node-Registry- und Definitionsrevision beziehungsweise
  Draft-Hash,
- fokussierten Entity-Typ und Entity-ID.

Die feste Prompt-Reihenfolge lautet:

1. `system_constraints`
2. `editor_location`
3. `workflow_summary`
4. `focused_entity`
5. `effective_configuration`
6. `validation_and_runtime`
7. `allowed_mutations`
8. `approved_evidence`
9. `rejected_evidence_summary`
10. `response_contract`

Kontext-ID und Prompt-Hash werden aus kanonischen Bytes berechnet. Der Hub
persistiert Referenzen, Versionen, Hashes, Reason-Codes und Entscheidungen;
Rohprompt und Repository-Volltext werden standardmäßig nicht dauerhaft
gespeichert. Der flüchtige Prompt im Task-Envelope wird nach Abschluss oder
Abbruch bereinigt.

## Evidence-Policy

Der Browser liefert keine Quellinhalte. Der Hub löst erlaubte SourceRefs gegen
Tenant, Scope, Revision und Provenienz auf und delegiert nur diese Allowlist.
Der Worker führt Retrieval und Prompt-Injection-Scan aus. Der Hub akzeptiert
das Ergebnis erneut nur, wenn Source-ID, Version, Tenant, Scope und
Provenienz-Digest mit der autorisierten Referenz übereinstimmen und der Status
`verified` ist.

Unbekannte, erfundene, tenantfremde, scope-fremde, revisionslose oder stale
Referenzen werden fail-closed abgelehnt. Interne Context-, Location- oder
Record-IDs werden nie als Source-ID behandelt. Eine HelpResponse darf nur
Evidence-IDs zitieren, die der Hub für genau diesen Prompt akzeptiert hat.
Abgelehnte Evidence gelangt nicht als Inhalt in den Prompt; nur Anzahl und
stabile Reason-Codes werden zusammengefasst.

## Assistant-Lebenszyklus und API

Alle Browser-Endpunkte liegen unter
`/api/visual-process/assistant/v1` und benötigen Nutzer-Authentifizierung.
`GET /capabilities` bleibt auch bei deaktiviertem Chat erreichbar und liefert
Flags sowie Limits. Der normale Ablauf ist:

1. `POST /contexts` friert einen revisionsgebundenen Context ein.
2. `POST /conversations` eröffnet eine Conversation für diesen Context.
3. `POST /conversations/<id>/questions` liefert HTTP 202 und eine persistente
   Request-ID; Idempotency-Key und Client-Request-ID verhindern Duplikate.
4. `GET /requests/<id>` liefert Queue-, Retrieval-, Inferenz- oder
   Terminalstatus. `cancel` und `retry` sind explizite Aktionen.
5. Ein Fokuswechsel erfordert
   `POST /conversations/<id>/context-switch` mit Bestätigung; eine laufende
   Anfrage wechselt ihren Snapshot nicht implizit.

Der Hub erzwingt höchstens zwei aktive Requests je Conversation und zwanzig
Requests je Principal und Minute. Timeouts, Cancellation, Retry und Recovery
werden auf persistente Hub-Tasks abgebildet. Der Worker kennt weder die
Conversation-Persistenz noch andere Worker.

## Patch-Governance

`WorkflowPatch` ist ein typisierter Vorschlag, keine Schreibberechtigung. Der
Hub prüft Contract-Version, Graph-/Draft-Hash, NodeDefinition, erlaubte
JSON-Pointer-Pfade, erwarteten Altwert, Secret-Felder und die vollständige
Graphvalidierung auf einer Kopie. Preview erzeugt nur Diff und
Validierungsfolgen. Side-Effects und Approval-Anforderungen stammen aus der
kanonischen Registry und werden als stabile Policy-Reason-Codes im Preview und
Audit ausgewiesen.

`POST /requests/<id>/patch-decisions` verlangt Patch-Hash und Entscheidung.
Interaktive Annahme verwendet `approval_mode=interactive` und
`confirmed=true`. Für vollständig automatische Clients existiert additiv
`approval_mode=hub_auto`; dieser Modus funktioniert ausschließlich bei
`VISUAL_PROCESS_AI_PATCH_AUTO_APPROVAL_ENABLED=true` und wird als
`patch_hub_policy_auto_approved` auditiert. Er überspringt keine Hash-, Draft-,
Registry-, Side-Effect- oder Graphvalidierung. Selbst eine angenommene
Entscheidung speichert den Graphen nicht: Ein UI- oder Headless-Client übernimmt
den validierten Preview als atomare EditorCommand-Transaktion und speichert ihn
danach über den bestehenden CAS-Pfad. Interaktive Bestätigung bleibt optional,
nicht technische Voraussetzung für Automation.

## Feature-Flags und Rollout

Alle Flags sind standardmäßig `false` und werden in dieser Reihenfolge
freigegeben:

| Variable | Wirkung | Voraussetzung |
| --- | --- | --- |
| `VISUAL_PROCESS_REGISTRY_INSPECTOR_ENABLED` | registry-gesteuerter Inspector und Palette | Contract-, Migration- und Editor-Gates |
| `VISUAL_PROCESS_HOVER_HELP_ENABLED` | rein lokale Hover-/Positionshilfe | UI-Isolation und Hover-Performance |
| `VISUAL_PROCESS_ASSISTANT_CHAT_ENABLED` | Context, Conversation, Retrieval und HelpResponse | CodeCompass-, Security- und Hub-Worker-Gates |
| `VISUAL_PROCESS_AI_PATCHES_ENABLED` | Preview und explizite Patch-Entscheidung | alle vorherigen Gates plus Patch-E2E |

`VISUAL_PROCESS_AI_PATCH_AUTO_APPROVAL_ENABLED` ist eine separate, standardmäßig
deaktivierte Hub-Policy. Sie schaltet nur den auditierten `hub_auto`-
Entscheidungsmodus frei und aktiviert weder Chat noch AI-Patches selbst.

Zusätzliche Betriebswerte sind
`VISUAL_PROCESS_ASSISTANT_RETRIEVAL_TIMEOUT_MS=5000` und
`VISUAL_PROCESS_ASSISTANT_MODEL_TIMEOUT_MS=120000`. Ein Flag darf nur aktiviert
werden, wenn die Contract-, Security-, Integration-, E2E- und
Performance-Reports vollständig vorliegen und grün sind. Ein fehlender Report
ist ein blockierender Status, kein implizites „passed“.

## Betriebsbudgets

| Bereich | Harte Vorgabe |
| --- | --- |
| Referenzgraph | 500 Steps, 1000 Edges, 100 Wiederholungen |
| Hover | 350 ms Delay; lokal p95 ≤ 100 ms; 0 Retrieval- und 0 LLM-Requests |
| Selected Context | höchstens 4 Ranges, 80 Zeilen je Range, 4096 Prompt-Tokens |
| Conversation Context | höchstens 8 Ranges, 120 Zeilen je Range, 12000 Prompt-Tokens, 12 Evidence-Items |
| Retrieval | warm p95 ≤ 2000 ms; harter Timeout 5000 ms |
| Assistant API | 2 in-flight je Conversation; 20 Requests/Principal/Minute; Modell-Timeout 120000 ms |
| Frontend | 1000 Fokuswechsel; stabilisiertes zusätzliches Heap-Wachstum ≤ 20 MiB |

Budgetüberschreitungen werden verworfen oder mit einem stabilen Fehlerstatus
beendet; Limits werden nicht durch Abschneiden sicherheitsrelevanter
Revisionsdaten „repariert“.

Der Hub projiziert Evidence vor der Prompt-Erzeugung deterministisch. Er
sortiert Referenzen stabil, entfernt Duplikate, begrenzt die Zahl von Ranges
und Evidence-Items und kürzt jede Range auf das Profil-Limit. Der konservative
Prompt-Schätzer rechnet vier Unicode-Zeichen je Token; passt der Prompt danach
noch nicht, werden die zuletzt sortierten Evidence-Items verworfen. Profil,
Limits, Anzahl der verworfenen Items, Kürzungen und stabile Reason-Codes stehen
unter `ananta.context_budget` im Context-Snapshot. Nur die nach dem finalen
Token-Budget verbleibenden Evidence-IDs erreichen das Worker-Envelope.

## Diagnose

- `GET /api/visual-process/assistant/v1/capabilities` zeigt die effektiven Flags
  und Laufzeitlimits.
- `GET /api/visual-process/assistant/v1/requests/<request_id>` zeigt persistenten
  Status und stabilen Error-Code.
- Prometheus stellt `visual_process_assistant_requests_total{status=...}` und
  `visual_process_assistant_active` bereit.
- Audit-Ereignisse `visual_process_assistant_task_queued`,
  `visual_process_assistant_worker_result_accepted` und
  `visual_process_assistant_cancelled` verbinden Hub-Request und Task, ohne
  Secrets oder Rohprompt dauerhaft zu protokollieren.
- Wiederholte `assistant_*_stale`, `*_scope_forbidden`,
  `*_prompt_hash_mismatch` oder `*_evidence_forged` sind Contract-/Security-
  Fehler und dürfen nicht automatisch retried werden.

## Rollback

Der sichere Rollback deaktiviert zuerst alle vier Flags und startet Hub sowie
Frontend mit unveränderter Datenbank neu:

```dotenv
VISUAL_PROCESS_REGISTRY_INSPECTOR_ENABLED=false
VISUAL_PROCESS_HOVER_HELP_ENABLED=false
VISUAL_PROCESS_ASSISTANT_CHAT_ENABLED=false
VISUAL_PROCESS_AI_PATCHES_ENABLED=false
VISUAL_PROCESS_AI_PATCH_AUTO_APPROVAL_ENABLED=false
```

Danach werden aktive Assistant-Requests abgebrochen beziehungsweise durch die
Timeout-Reconciliation terminalisiert. Die additiven Tabellen und
Versionsspalten werden beim normalen Feature-Rollback nicht entfernt: Der
bisherige Editor ignoriert sie, gespeicherte Graphen und Runtime-Overlays
bleiben lesbar. Erst wenn die alte Anwendungsversion die Daten erfolgreich
öffnet, bearbeitet, validiert und wieder speichert, darf eine separate
Datenbankmigration erwogen werden.

Rollback gilt als erfolgreich, wenn der bestehende Editor, Legacy-Graphen mit
`run_state`, unbekannte Kinds/Felder und die Read-only-Runtime-Ansicht bei vier
deaktivierten Flags weiter funktionieren. Patch-Preview- und Chat-Endpunkte
müssen dann fail-closed „feature disabled“ liefern; bestehende Graphdefinitionen
dürfen nicht verändert werden.

## Test- und Release-Gates

Der Baseline-Report belegt Quellcode-Parität. Funktions- und Performance-Gates
sind davon getrennt: Ein Quelleninventar beweist keine ausgeführte E2E-
Interaktion. Der Release-Gate-Generator führt deshalb keine fehlende Evidence
als Erfolg fort. Er aggregiert nur explizite, revisionsgebundene Ergebnisse und
blockiert bei fehlenden Contract-, Hub-Worker-, Security-, Angular-E2E- oder
Performance-Nachweisen.

Die Architektur trennt damit Verantwortlichkeiten bewusst (SRP): Context-
Kanonisierung, Task-Orchestrierung, Worker-Ausführung, Evidence-Autorisierung
und Patch-Validierung besitzen eigene Ports. Die bestehende modulweite
Service-Locator-Komposition bleibt als kompatible Integrationsnaht erhalten;
neue Fachlogik hängt an injizierbaren Services und nicht an Worker- oder
Frontend-Implementierungen (DIP).

Die lokalen, netzwerkfreien Betriebsproben werden separat ausgeführt und
anschließend fail-closed aggregiert. Die Browser-Journeys verwenden eine
lokale Testidentität und vollständig kontrollierte HTTP-Routen; sie benötigen
keinen laufenden Hub. Nach einem produktiven Frontend-Change wird zuerst neu
gebaut und der SPA-fähige Static Server in einem separaten Terminal gestartet:

```bash
cd frontend-angular
npm run build
python ../scripts/spa_static_server.py dist/ananta-angular/browser --host 127.0.0.1 --port 4201
```

Danach werden Browser-, Backend- und Matrix-Evidence gegen denselben
eingefrorenen Source-Stand erzeugt:

```bash
cd frontend-angular
E2E_FRONTEND_URL=http://127.0.0.1:4201 npx playwright test --config=playwright.vpa-performance.config.ts
cd ..
python scripts/run_visual_process_assistant_performance_gate.py
E2E_FRONTEND_URL=http://127.0.0.1:4201 python scripts/run_visual_process_assistant_functional_gate.py
python -m pytest -q tests/benchmarks/visual_process_assistant/test_operational_budgets.py
python scripts/generate_visual_process_assistant_gates.py --check
python scripts/generate_visual_process_assistant_acceptance_matrix.py
python scripts/generate_visual_process_assistant_acceptance_matrix.py --check
```

Der Runner misst 500/1000 und 1000/2000 Step-/Edge-Graphen, 100 warme reale
CodeCompass-Abfragen sowie beide Context-Profile. Die rohe, an einen
Worktree-Hash gebundene Evidence liegt in
`artifacts/test-gates/visual-process-assistant-performance-evidence.json`; der
aggregierte Status in
`artifacts/test-gates/visual-process-assistant-performance.json`. Der echte
Chromium-Lauf schreibt seine quellspezifischen Hashes und Fokus-, Heap- sowie
Subscription-Messwerte zuerst nach
`artifacts/test-gates/visual-process-assistant-frontend-performance-evidence.json`.
Der Performance-Runner akzeptiert diese Browser-Evidence nur, wenn jeder
abgedeckte Frontend-Hash noch aktuell ist.

Functional Evidence liegt separat unter
`artifacts/test-gates/visual-process-assistant-functional-evidence.json` und
ist an eine exakte Source-Projection einschließlich erwarteter, noch fehlender
Suites gebunden. Ein positives Grounding-Gate wird niemals aus einer Fixture-
Identität abgeleitet: Fehlt eine extern bereitgestellte und verifizierte
Source-Autorität, bleibt `grounded_source_authority_positive` ausdrücklich
blockiert, auch wenn alle negativen Security-Gates erfolgreich sind.

Sind `ANANTA_TEST_AUTHORIZED_SOURCE_ID` oder
`ANANTA_TEST_AUTHORIZED_SOURCE_IDS` ausdrücklich von der externen Authority
bereitgestellt, startet der Functional-Runner den CodeCompass-Generator mit
`--positive-authority` in einem temporären Ausgabeverzeichnis. Er akzeptiert
nur einen verifizierten Report, dessen freigegebene Anzahl exakt den
bereitgestellten Identitäten entspricht und der keine Source-ID synthetisiert.
Der positive Report wird absichtlich nicht als committed Fixture gespeichert;
ohne diese Umgebung bleibt die Suite `not_run` und der Rollout fail-closed.
