# ADR: Worker Sandbox Balanced Execution

## Status

Accepted

## Context

Der ananta-worker soll im Sandbox-Betrieb deutlich produktiver werden, ohne die Hub-Ownership fuer Policy, Approval und Audit zu brechen.  
Gleichzeitig darf der Laufzeitpfad keine User-Interaktion voraussetzen; der Betrieb ist vollautomatisch.

## Decision

1. **Harte Invarianten bleiben unverhandelbar**:
   - Schema-Gates fuer Ingress/Egress-Artefakte
   - Command-/Capability-Allowlist mit Risiko-Klassifikation
   - harte Loop-/Runtime-/Context-Budgets
   - Trace-Metadaten (`trace_id`, `task_id`, `capability_id`, `context_hash`, `policy_decision_ref`)
2. **Ausfuehrung erfolgt profilbasiert** mit `safe`, `balanced`, `fast`.
3. **`balanced` ist Default**, `safe` und `fast` sind explizite Betriebsmodi.
4. **Keine interaktive Freigabe im Runtime-Flow**; Freigabeentscheidungen erfolgen ueber Hub-Tokens und Policy.
5. **Hub bleibt Control Plane**; Worker fuehrt delegierte Ausfuehrung aus.

## Profile

- `safe`: maximale Konservativitaet, minimale automatische Erleichterungen.
- `balanced`: produktiver Standard, auto-allow fuer klar read-only Diagnostik innerhalb Policy-Rahmen.
- `fast`: hoehere Budgets und aggressivere Iteration, aber unveraenderte harte Invarianten.

## Consequences

- Produktivitaet steigt im Alltag durch geringere Friktion in `balanced`.
- Security- und Audit-Baseline bleibt durch unveraenderte Invarianten stabil.
- Der Worker kann sowohl Hub-integriert als auch standalone betrieben werden, solange derselbe Vertragskern eingehalten wird.
