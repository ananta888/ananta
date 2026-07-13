# Workflow runtime capabilities and selection

The Hub owns runtime discovery, eligibility decisions, auditing and task
delegation. Native, LangGraph and Temporal implementations execute delegated
work, but are not imported by the Hub control or surface layers.

## Versioned capability matrix

`config/workflow_runtime/runtime_capability_matrix.v1.json` is the versioned
declaration consumed by `WorkflowRuntimeCapabilityService`. Each runtime entry
declares:

- runtime and execution-contract versions;
- `live` or `durable` mode;
- capabilities and explicit operational restrictions;
- health state plus a stable health reason code;
- allowed data localities, policy versions and budget ceilings.

The service implements the read-only catalog and health ports used by runtime
selection. Its `hub_projection()` is the common data shape for HTTP, CLI, TUI
and Angular consumers. Surfaces consume this projection; they do not import
Native, LangGraph, Temporal or worker classes.

The checked-in health state is a declared bootstrap value. A deployment may
inject a live `RuntimeHealthPort`, but it must preserve the same `ready`,
`degraded`, `unavailable` and `disabled` states and reason-code contract.

## Selection profiles

`config/workflow_runtime/runtime_selection_profiles.v1.json` defines immutable
Hub profiles with all four required controls:

- `preferred_runtime`;
- `allowed_runtimes`;
- `required_capabilities`;
- `explicit_fallback_policy`.

Supplying a profile and ad-hoc runtime overrides in one start request is
rejected as `runtime_profile_override_denied`. This prevents a caller from
widening an administrator-owned profile.

## Eligibility and fallback

`WorkflowRuntimeSelectionService` evaluates every registered candidate in a
deterministic order. Independent ports check capability intersection, policy
version, data locality, budget capacity and health. A candidate failing any
check is ineligible. The decision audit contains the selected runtime and every
rejected alternative with stable reason codes.

A non-preferred runtime can be selected only when the profile explicitly names
it as a fallback and the shared fallback policy proves semantic equivalence.
Any capability loss is denied; loss of authorization, policy, durability,
resume, side-effect protection or audit is always protected. There is no local
or in-process fallback.

No eligible candidate yields one of two fail-closed modes:

- `incompatible` when the declared runtime contract cannot satisfy requested
  capabilities;
- `blocked` when health, policy, locality, budget, fallback governance or audit
  prevents safe execution.

`WorkflowControlService` never calls its Hub task bridge for either mode.

## Stable reason-code families

| Family | Meaning |
| --- | --- |
| `runtime_capabilities_*` | Required execution semantics are absent. |
| `runtime_health_*` | Live or declared health is not acceptable. |
| `runtime_policy_*` | Hub policy does not authorize the candidate. |
| `runtime_data_locality_*` | Required data placement is unavailable. |
| `runtime_budget_*` | Capacity is unknown or below the requested budget. |
| `runtime_fallback_*` | An alternative is not explicit or not equivalent. |
| `runtime_selection_*` | Final selection or fail-closed outcome. |

The catalog, health, policy, locality, budget and audit interfaces are narrow
ports. This separation keeps selection policy testable and protects SRP and
DIP: adding a runtime or health provider does not modify the Hub control flow.

## Hub/worker boundary for Native graphs

Native node wire contracts and the Hub task-queue port live under
`agent.services.workflow_runtime`. Worker modules re-export or implement those
contracts for compatibility. Production Hub services must never import
`worker.*`; a boundary test enforces this rule. Workers receive one delegated
node command and cannot obtain the Hub queue port, so they cannot create their
own orchestration loop.
