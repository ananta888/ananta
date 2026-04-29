# Worker Standalone Mode

## Zweck

Standalone erlaubt die Nutzung des Workers ohne kompletten Hub-Stack, mit stabilen Datei-/Diff-Verträgen.

## Boundary Contract

- Eingabe: `standalone_task_contract.v1`
- Minimale Kontrollfelder: `trace_id`, `capability_id`, `context_hash`
- Payload bleibt artefaktzentriert (`files`, `diffs`, `command`)

## Architektur

- Core-Loop nutzt Ports (`PolicyPort`, `TracePort`, `ArtifactPort`)
- Hub-Integration ist Adapter, nicht Hard-Dependency
- Gleiche harte Invarianten wie im Hub-Modus (Schema, Policy, Budgets, Traceability)

## Kompatibilität

- Gleiche degradierte Gründe und Tracesemantik wie im integrierten Modus
- Migration: Hub-Modus <-> Standalone über denselben Core-Loop
- Kein User-in-the-loop zur Laufzeit

