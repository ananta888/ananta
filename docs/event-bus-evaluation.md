# Event Bus Evaluation (Post-Baseline)

## Was ein Event Bus loesen kann

- Entkopplung von Wakeup-Signalen ueber mehrere Prozesse/Container.
- Robustere Event-Verteilung bei horizontaler Skalierung.
- Replay/Retry fuer Events mit idempotenten Handlern.

## Was ein Event Bus nicht loest

- Falsche Capacity- oder Security-Policy.
- Unbegrenzte Parallelitaet ohne Backpressure.
- Workspace-Konsistenzprobleme ohne Locking/Ownership.

## Operative Kosten

- Zusatzzustand (Redis/NATS/Kafka) mit Backup/Monitoring.
- Event-Schema- und Versionierungsaufwand.
- Fehlerbilder wie Duplicate-Events, Lag, Partitionierung.

## Entscheidung fuer aktuellen Stand

- P0/P1 Fixes sind **nicht** von Redis/Event-Bus abhaengig.
- Vor Event-Bus zuerst: bounded concurrency, klare reason_codes, Messbarkeit und Locking stabil halten.
