# Hotspot Review Matrix

This matrix defines higher review and test expectations for files that carry central orchestration, bootstrap, routing, or UI-state risk.

| Hotspot | Risk | Required Review Focus | Minimum Verification |
| --- | --- | --- | --- |
| `agent/ai_agent.py` and `agent/bootstrap/` | Hub startup, route exposure, background lifecycle | Startup phase order, error visibility, no worker orchestration | Bootstrap order tests, extension loading tests, route inventory check |
| `agent/services/task_orchestration_service.py` | Hub task ownership and delegation flow | Hub remains control plane, workers do not orchestrate workers, state transitions stay explicit | Orchestration/read-model tests, completion-path regression tests |
| `agent/services/service_registry.py` | Hidden service coupling | Explicit dependency boundaries, no broad cross-domain calls without a facade | Registry construction tests, import/cycle checks |
| `frontend-angular/src/app/components/dashboard.component.ts` | Large smart component with polling and aggregation | Component split, ViewModel ownership, no duplicate refresh loops | Component/facade tests, dashboard read-model contract checks |
| `frontend-angular/src/app/services/auth.interceptor.ts` | Auth transport and retry behavior | Hub user auth and worker-agent JWT paths remain separate | 401 refresh tests, agent-target resolver tests |

Reviewers should call out preserved or introduced SOLID violations explicitly. Large hotspot edits should be split so extraction and behavior changes are separately reviewable.

