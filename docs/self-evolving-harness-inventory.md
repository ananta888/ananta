# Self-Evolving-Harness — Bestandsaufnahme und Integrations-Mapping (SEH-001)

Ergebnis der repositoryweiten Suche nach vorhandenen Evolver-, Planner-, Worker-, Gate-,
Benchmark- und Tracing-Pfaden. Grundregel: **keine neue Kernkomponente vor Identifikation
vorhandener Äquivalente** (Design-Prinzip „integrieren statt parallel bauen“). Dieses Dokument ist
die verbindliche Mapping-Tabelle; neue SEH-Module hängen ausschließlich an diesen Punkten.

## Mapping-Tabelle

| SEH-Bereich | Vorhandene Implementierung | Ownership | Wiederverwendung | Duplikationsrisiko / Migration |
|---|---|---|---|---|
| Native Prompt-Evolution | `agent/services/planning_prompt_optimizer_service.py`, `agent/services/planning_prompt_evolver_service.py` | Planner | Als **Engine** `native_prompt` hinter dem Genome-Vertrag; Mutationsoberfläche bleibt Planning-Prompts | Nicht in `PlanningPromptEvolverService` hineinwachsen lassen; SEH bekommt getrennte typisierte Mutation (`harness_genome`) |
| Prompt-Provenienz | `agent/services/prompt_provenance.py`, `agent/services/prompt_trace_service.py`, `agent/services/prompt_trace_access_policy.py` | Prompt/Trace | Provenance- und Trace-Refs als Genome-/Run-Bindung | Kein zweites Trace-Format; SEH referenziert `trace_ref` |
| DSPy-Optimierung | `docs/integrations/dspy-optimization-engine.md`, `agent/routes/dspy_optimization.py`, `worker/optimization/dspy/engine_adapter.py`, `docker/compose-next/requirements.dspy-optimization.lock`, `ananta_contracts/dspy_optimization.py`, `ananta_contracts/dspy_program_registry.py` | DSPy-Worker | Als **Engine** `dspy` (und `gepa` via Capability-Registry) hinter einem Ananta-Engine-Adapter | GEPA nicht als zweite Runtime; über DSPy-Capability führen |
| DSPy-Promotion/Gate | `agent/services/dspy_promotion_service.py`, `agent/services/dspy_release_gate.py` | Gate/Promotion | Wiederverwenden für Kandidaten-Promotion | Kein eigener Promotion-Pfad im SEH |
| EvoMap/Evolver | `plugins/evolver_adapter/` (`adapter.py`, `mapper.py`), `services/evolver_bridge/`, `docs/evolver-adapter.md`, `docker/old_way/Dockerfile.evolver-bridge`, `agent/services/evolution_proposal_service.py`, `agent/services/evolution_run_service.py`, `agent/services/evolution_service.py` | Evolver | Als **Engine** `evomap`; Proposals → generische `EvolutionProposal`/`EvolutionCandidate` | Analyze/Proposal-only bleibt Default; kein Apply ohne separates Gate |
| Evolution-Kern | `agent/services/evolution/` (`engine.py`, `registry.py`, `models.py`, `context_builder.py`, `payload_redaction.py`) | Evolution | Basis für Mutation-/Proposal-Normalisierung | Nicht neu implementieren; um Genome/Candidate erweitern |
| Mutations-Gate | `agent/services/mutation_gate_service.py`, `agent/services/hub_evidence_gate_service.py` | Governance | **Immutable** Gate-Referenzen im Genome; SEH-Gates werden hier registriert | Gates niemals im Genome inline/mutierbar |
| Benchmark-Runner | `agent/services/benchmark_runner_service.py`, `agent/services/benchmark_suite.py`, `agent/services/hub_benchmark_service.py`, `agent/routes/hub_benchmark.py`, `agent/services/benchmark_job_service.py` | Benchmark | Benchmark-Runs als `HarnessBenchmarkRunDB` (SEH-004) | Holdouts/Evaluatoren bleiben außerhalb des Genomes |
| Bayesian/Benchmark-Estimation | `agent/services/bayesian_benchmark_estimator.py`, `agent/services/bayesian_benchmark_adapters.py` | Benchmark | Statistische Robustheit (SEH-016) | Kein neuer Schätzer |
| Promotion/Rollback | `agent/services/custom_tool_promotion_service.py`, `…promotion_service.py` (dspy/verification/research/specialist), `agent/services/sfu_broadcast_rollback_service.py`, `agent/services/scientific_skill_catalog_rollback_service.py` | Promotion | Promotion-/Rollback-Controller (SEH-018) als Orchestrierung über diese Dienste | Kein zweiter Rollback-Mechanismus |
| Lineage/Provenance (ML) | `agent/services/research_training_lineage_service.py`, `agent/services/ml_intern_speech_adapter_lineage.py`, `agent/services/ml_intern_provenance_contract.py`, `agent/services/release_evidence_provenance_service.py` | ML-Intern | Muster für Append-only-Lineage; SEH-Store (SEH-004) analog | Gemeinsames Hash-/Digest-Muster (`content_digest`) |
| Worker/Todo-Verträge | `worker/`, `worker/training/contracts.py`, `agent/repositories/harness_evolution.py` (neu) | Worker | Sandbox-Runner (SEH-010) nutzt bestehende Worker-Isolation | Worker orchestrieren nie; Hub bleibt Control Plane |
| Tool/MCP-Pfad | `agent/services/hub_tool_gateway.py`, `agent/routes/worker_tool_gateway.py`, `agent/services/ananta_tool_argument_docs.py` | Hub | Tool-Policy (Genome `tools`) bindet an bestehende Allowlist/Capability | Genome darf Rechte nur verengen, nie erweitern (SEH-003) |
| Context-Profile (32k/64k/128k) | `agent/context_profile.py` (`effective_window_tokens`), `docs/jev-llamacpp-decision-mode.md` | Context | Konfigurierbarer Suchraum (SEH-029) über Capability-Intersection | Keine universelle Annahme fester Budgets |
| Context-Cache/Compaction | `agent/services/pre_model_context_cache.py`, `agent/services/context_bundle_service.py`, `agent/services/context_assembly_trace_service.py` | Context | Reducer/Compactor (SEH-008) und Observation Archive (SEH-006) | Evidence-preserving Reducer mit Verify-Fallback |
| CodeCompass | `agent/services/codecompass_*` | CodeCompass | Retrieval-Policy-Profile (Genome `retrieval.profile`) | Nur freigegebene ContextBundles zuführen |
| Tracing | `agent/services/delegation_trace_service.py`, `agent/services/codecompass_context_trace_service.py`, `agent/services/task_engine_trace.py`, `agent/services/workflow_terminal_trace_reconciliation.py` | Trace | `trace_ref` je Benchmark-Run | Kein zweites Trace-Format |

## Datenfluss (Ziel)

```
Goal → Hub Planner → HarnessGenome (Registry/Store, SEH-002/004)
     → HarnessRuntime (SEH-003: typisierte Strategie-Ports)
     → Engine (native_prompt | dspy | gepa | evomap) liefert EvolutionCandidate
     → Sandbox Runner (SEH-010) → Benchmark Runner + Gate Engine (SEH-013/014)
     → Pareto Selector (SEH-015) → Promotion/Rollback Controller (SEH-018)
     → Lineage/Experiment Store (SEH-004, append-only, content-hashed)
```

## Erweiterungspunkte

- **Genome**: `ananta_contracts/harness_genome.py` (Sektionen, kanonische ID, Diff, Migration).
- **Runtime**: `agent/services/harness_runtime.py` (Strategie-Familien, Gate-/Capability-Invarianten).
- **Store**: `agent/repositories/harness_evolution.py` + `agent/db_models/harness_evolution.py`.
- **Engines**: über `StrategyRegistry`/Engine-Capability-Registry, nicht über Task-Namen.

## Duplikationsrisiken und Migrationspfade

1. **Prompt-Evolver** darf seinen Mutationsumfang **nicht** still auf Harness-Ebenen ausdehnen
   (explizite Grenze in `existing_foundation.important_boundary`). Harness-Mutationen laufen über
   `harness_genome`.
2. **GEPA** wird zuerst über die DSPy-Capability geführt; eine eigene Runtime nur mit Benchmark-Beleg.
3. **EvoMap** bleibt Proposal-only, bis Apply separat admitiert ist (SEH-027/043).
4. **Promotion/Rollback** wird orchestriert, nicht neu gebaut; bestehende Gates bleiben autoritativ.
5. **Lineage-Formate** (ML-Intern) und SEH-Store teilen das Hash-Muster, bleiben aber getrennte Tabellen.

## Nicht-mutierbare Oberfläche (außerhalb jedes Genomes)

Benchmark-Holdouts, Evaluatoren, Security-Enforcement, Budget-Caps, Promotion-Autorität, Auth,
Secrets, Audit und die Gate-Registry (`GateRegistryPort`). Ein Genome kann diese nur **referenzieren**.

## Kontrollierte Composition (SEH-012, Teilimplementierung)

`agent/services/harness_crossover.py` kombiniert zwei vom Hub gewählte Verbesserungen
gegen ein explizites gemeinsames Basisgenom. Der Hub bleibt für Auswahl aus der Pareto-Front,
registrierte Herkunft, Dispatch und Promotion verantwortlich. Composition führt keine Tools aus.

- Disjunkte Feldänderungen lassen sich auch innerhalb einer Sektion zusammenführen.
  Überlappungen werden einschließlich identischer Änderungen als Konflikt abgelehnt;
  Listen/Tool-Allowlisten werden atomar behandelt.
- Gates, Evidenzpflicht, Secret-Referenzen, Memory-Scopes und Code-/Config-Snapshots
  bleiben unverändert. Tool-Rechte dürfen nur verengt werden. Opaque Code-Hashes
  werden niemals zusammengeführt; Code-Composition benötigt später einen Patch-Adapter.
- Das Kind erhält beide Parent-Digests und den Basis-Digest als Herkunft, aber keine
  geerbten Performancewerte. Es wird nach dem Merge erneut als vollständiges Genom validiert.
- `CrossoverEvaluator` verlangt eine neue Beobachtung über `CrossoverBenchmarkPort`.
  Genom und content-addressed Suite müssen exakt passen; vollständige Task-Abdeckung
  einschließlich Holdouts, endliche Qualitäts-/Ressourcenmetriken und neue Hard-Gate-Ergebnisse
  sind Voraussetzung für die Auswahlfähigkeit. Benchmarkfehler erzeugen kein auswählbares Kind.

**Produktive Verdrahtung offen:** Der injizierte Benchmark-Port muss über die bestehenden
Hub-Task-/Lease-/Evidence-Pfade und den noch offenen Sandbox-Runner (SEH-010) implementiert
werden. Der Dienst ist deshalb nicht automatisch im Runtime-Pfad aktiviert. Beobachtungen
und die deterministischen Tests sind technische beziehungsweise synthetische Ergebnisse;
sie sind keine registrierte Produktions-Release-Evidenz. Es werden keine SRC-/RUN-IDs erzeugt.

**SOLID-Prüfung:** Reine Composition und Evaluation sind getrennt (SRP); der Evaluator
nutzt einen schmalen, injizierten Benchmark-Port (ISP/DIP). Bestehende Genom-, Suite- und
Gate-Verträge werden wiederverwendet. Die bisherigen konkreten In-Memory-Gate-/Suite-Objekte
bleiben bestehen; persistente Registry-/Runtime-Anbindung ist weiterhin offene Integrationsarbeit.

## Offline-Sandbox und Hub-Evidence-Anbindung (SEH-010/023, Teilimplementierung)

`HarnessSandboxManifest` (`ananta_contracts/harness_sandbox.py`) bindet Image-Digest,
Repository-Commit, Genom, vollständige Benchmark-Suite, Benchmark-Quelltext, Seed und Limits
content-addressed. Keine Host-Pfade oder freigeformten Docker-Optionen gehören zum Vertrag.
`worker/harness/sandbox.py` führt genau einen delegierten Offline-Python-Benchmark aus:

- eigener Container mit gepinntem Image, `--pull never`, nicht privilegiertem User,
  ohne Capabilities, Netzwerk, GPU-Zuweisung, Host-Secrets oder Tool-Gateway;
- ausschließlich vier vom Manifest abgeleitete, schreibgeschützte Dateien als Inputs;
  der aktive Host-Arbeitsbaum wird weder kopiert noch gemountet;
- CPU-Zeit/-Anteil, RAM inklusive Swap, Prozesszahl und temporärer Speicher sind begrenzt;
- Timeout, Cancellation und Ausgabeüberlauf beenden den CLI-Prozess und erzwingen danach
  Container-Cleanup. Fehlendes Runtime/Cleanup gibt einen begrenzten Fehler zurück;
  es existiert kein Host-Ausführungsfallback;
- Ausgabe wird beim Lesen begrenzt, nicht erst nach unbeschränktem `communicate()`.
  `worker/core/bounded_process.py` nutzt dazu den bestehenden `ProcessGroupController`.

`agent/services/harness_sandbox_service.py` hält Admission, SOURCE-/RUN-Reservation,
Assignment-/Manifestprüfung, Vollständigkeitsprüfung und Gate-Auswertung beim Hub.
Der bestehende `HubEvidenceGateService` und die Registry werden wiederverwendet.
Der Worker-Port ist für einen bereits autorisierten Hub-Task und dessen exakte Lease;
der Worker erzeugt keine Tasks und keine Evidenzidentitäten.

Die automatisierten Tests verwenden ausdrücklich synthetische Test-Evidenz und können
keine Produktionsfreigabe erfüllen. Lokaler Container-Test (kein Image-Pull, bounded):

```sh
ANANTA_HARNESS_LIVE_SANDBOX=1 python -m pytest tests/harness/test_harness_sandbox.py --timeout=45
```

Das Testimage ist derselbe gepinnte Python-Basis-Digest wie beim vorhandenen DSPy-Worker.
Fehlt es lokal oder ist Docker nicht verfügbar, scheitert der explizite Container-Test.

**Offen:** produktiver Task-Transport/Handler, Hub-metered LLM-/API-/Token-Gateway und
GPU-Lanes. Offline-Runs haben API-/Model-/Tool-Budget null; solche Ressourcen werden
nicht implizit durch Netzwerkfreigabe aktiviert. Diese Grenzen verhindern ein vorzeitiges
Done für den gesamten SEH-010-Task.

**SOLID:** Manifestvalidierung, Prozesskontrolle, Container-Infrastruktur und Hub-Admission
sind getrennte Verantwortlichkeiten. Kleine injizierte Ports erlauben deterministische
Failure-Tests. Der vorhandene ProcessGroupController liegt noch im Training-Namespace
(bewahrte DIP-Kopplung); eine spätere gemeinsame Worker-Core-Fassade kann diesen Import
ersetzen. Der bisherige Research-Code-Sandbox-Adapter bleibt unverändert und benötigt
separat die Migration auf die begrenzte Prozess-/Cleanup-Infrastruktur.

## Persistierter Hub-Suchlauf (SEH-011/020, Teilimplementierung)

`HarnessSearchService` besitzt keine Hintergrundschleife. Jeder `advance` führt genau
einen begrenzten Hub-Schritt aus: ready → dispatch_pending → awaiting_result → ready,
beziehungsweise cancel_pending → finished/containment_failed. Persistierte, append-only
SQL-Revisionen und Unique Keys verhindern, dass zwei konkurrierende Schritte denselben
Zustandswechsel übernehmen. Tenant/Project sind bei jedem Zugriff erforderlich.

Der Hypothesengenerator verwendet ausschließlich typisierte Telemetrie: Truncation zählt
als Hypothese für größere, vom realen Modelllimit unterstützte Context-Budgets;
explizit sichere Fusion-Gelegenheiten als Hypothese für Action Fusion. Seed und Attempt
bestimmen Exploration/Exploitation reproduzierbar. Bereits versuchte Ideen werden über
Neustarts hinweg ausgeschlossen. Mutationen erhalten Hypothese, Parent und Diff.

Die unveränderliche Suchpolicy liegt außerhalb des Genoms. Run-/Zeit-/Token-/Kostenlimits
werden vor dem persistierten Dispatch-Intent geprüft; jeder Run reserviert konservativ
seine maximalen Kosten/Tokens, ohne unbekannte oder fehlgeschlagene Arbeit zu erstatten.
Die Implementierung fährt seriell und überschreitet dadurch keinen Parallelitätsdeckel.
Der injizierte Hub-Task-Transport muss Submit nach Dispatch-Key idempotent machen,
Caps und absolute Deadline am tatsächlichen Dispatch erzwingen und Poll ohne Warten
zurückgeben. Verlorene Acknowledgements dürfen keinen zweiten Task erzeugen.

Ergebnisse werden ausschließlich nach `HubEvidenceRegistryService.require_run_result`
unter exakter Assignment-/Lease-/Input-/Result-Bindung ausgewertet. Unregistrierte,
mutierte oder fremde Resultate lösen Containment aus. Nur registrierte technische Runs
mit bestandenem Gate und vollständigen Objective-Metriken können in die lokale
Pareto-Population gelangen; ihre Registry-Referenzen und Testklassifikation bleiben
gespeichert. Auswahl ändert nur den Such-Parent, niemals die produktive Baseline.

Kill-Switch, abgelaufene Deadline und erschöpfte Budgets stoppen neue Kandidaten.
Aktive Intents müssen idempotent gecancelt und tatsächlich contained werden. Nach
begrenzten erfolglosen Containment-Versuchen bleibt ein maschinenlesbarer Fehler
mit dem aktiven Dispatch-Key erhalten; keine weitere Arbeit und kein menschliches Warten.

**Offen:** produktiver Hub-Queue-Adapter, Scheduler-/Operator-Mutationsverdrahtung,
weitere Engine-Hypothesen und systemweite/gemeinsame Quota-Integration. Die neue
Zustandsmaschine ist kein Ersatz für bestehende Task-Ownership oder Approval-Policy.
Die Tests verwenden synthetische, von der echten Registry registrierte Runs und decken
Neustarts, Ack-Verlust, konkurrierende Revisionen, Budget, Kill-Switch, Timeout und
mutierte Evidenz ab. Alle Schritte sind headless und bounded.

**SOLID:** Vorschläge, Suchpolicy, Zustandsübergänge, Persistence und Task-Transport
liegen in eigenen Modulen. Der Controller hängt an kleinen Ports (SRP/ISP/DIP);
er besitzt weder Docker-Implementierung noch Worker-Tools oder Release-Autorität.
