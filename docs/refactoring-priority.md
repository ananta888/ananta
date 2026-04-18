# Refactoring Priority

Refactoring priority is risk-based, not feature-demand-based. The recommended order in `todo.json` should be read through these factors:

| Factor | Weight | Rationale |
| --- | --- | --- |
| System centrality | High | Hub startup, task orchestration, registry, auth and dashboard flows have broad blast radius. |
| Change frequency | High | Frequently edited hotspots accumulate coupling fastest. |
| Hidden side effects | High | Startup, polling, completion and auth retry paths need explicit sequencing. |
| Test isolation | Medium | Work that creates smaller units and clearer seams unlocks later changes. |
| Contract drift risk | Medium | Backend read-models and frontend DTOs need mechanical checks before large UI changes. |

Near-term order:

1. Stabilize hub bootstrap, route inventory and startup telemetry.
2. Split service registry and task orchestration hotspots behind narrow facades.
3. Split dashboard/UI state and introduce typed frontend read models.
4. Harden auth transport and plugin/runtime reports.
5. Add soft guardrails to keep new hotspots visible without blocking routine work.

This preserves the hub-worker architecture: hub owns planning, routing, governance and task queues; workers execute delegated work only.

