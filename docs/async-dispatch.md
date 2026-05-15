# Async Dispatch Protocol (Feature-Flagged)

## Ziel

Propose-Dispatch vom Hub an Worker entkoppeln, damit Tick-Latenz nicht durch lange Worker-Propose-Aufrufe blockiert.

## Flag

- `autopilot.async_dispatch_enabled` (Default: `false`)

## Verhalten

Wenn aktiviert:

1. Hub queued Dispatches in ThreadPool.
2. Tick wartet nicht auf Fertigstellung jedes Dispatches.
3. Worker aktualisieren Task-Status weiterhin ueber bestehende `/step/propose` + `/step/execute` Flows.
4. Fehler in asynchronen Dispatch-Threads werden auf Task als `failed` gesetzt.

## Safety

- Security-Caps und Effective-Concurrency bleiben unveraendert aktiv.
- Kein Worker-zu-Worker Routing.
- Hub bleibt Owner von Queue, Policy und Delegation.

## DoD

- Flag aus: bisheriges synchrones Verhalten.
- Flag an: Tick-Loop bleibt responsive unter langen Propose-Laufzeiten.
- Fehlerpfade bleiben auditiert und statuskonsistent.
