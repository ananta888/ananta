# Heuristic Runtime — Architecture

## Overview

The Heuristic Runtime provides a local, deterministic fallback layer for both the TUI Snake surface and the Chat CodeCompass surface. It runs without any LLM or network dependency and activates when the AI worker is unavailable, slow, or when a TTL-based HeuristicDecisionLease is still valid.

```
User action / Event
      │
      ▼
┌─────────────┐    ai_status=available   ┌────────────────┐
│  EventBus   │ ──────────────────────▶  │  AI Worker     │
│ (Observer)  │                          │ (ananta-worker) │
└──────┬──────┘                          └───────┬────────┘
       │ event                                   │ HeuristicToolCall
       ▼                                         ▼
┌─────────────────────────────────────────────────────────┐
│                  Decision Gate                          │
│                                                         │
│  ai_status==available  ──▶  PROPOSE_AI (yield to AI)   │
│  ai_status==offline    ──▶  heuristic path              │
│  ai_status==timeout    ──▶  heuristic path              │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  HeuristicDecisionLease│  (TTL: 5–10s snake, 10–20s chat)
              │  (T02.03)              │
              └────────────┬───────────┘
                           │  active lease valid?
               ┌───────────┴────────────┐
               │ YES                    │ NO / expired
               ▼                        ▼
     ┌──────────────────┐   ┌──────────────────────────┐
     │  Strategy        │   │  LeaseReevaluationService │
     │  (T03.01)        │   │  (T02.04)                 │
     └────────┬─────────┘   └────────────┬──────────────┘
              │                          │
              ▼                          │  context_hash changed?
     ┌──────────────────┐           ┌────┴────────────┐
     │  RuleChain /     │           │ extend / switch │
     │  ChatSelectors   │           │ candidate prop. │
     │  (T03.02)        │           └─────────────────┘
     └────────┬─────────┘
              │
              ▼
     ┌──────────────────┐
     │  StateMachine    │  (T03.03)
     │  (snake/chat)    │
     └────────┬─────────┘
              │
              ▼
     ┌──────────────────┐
     │  Command         │  (T03.05)
     │  (HeuristicCmd)  │
     └────────┬─────────┘
              │
              ▼
     ┌──────────────────┐
     │  CommandAdapter  │  TuiCommandAdapter / EclipseCommandAdapter
     └──────────────────┘
```

## Key Components

### HeuristicRegistry (`heuristic_registry_service.py`)
Loads `HeuristicDefinition` records from `heuristics/index.json`. Only `status=active` heuristics are available at runtime. Rollback and quarantine move files between `active/`, `archive/`, and `quarantine/` directories.

### HeuristicDecisionLease (`heuristic_lease_repo.py`)
TTL-based lease controlling which heuristic is currently active:
- Snake domains: 5–10s
- Chat domain: 10–20s
- Statuses: `active` → `expired` / `superseded` / `released`

### LeaseReevaluationService (`lease_reevaluation_service.py`)
Evaluates context hash + TTL expiry to produce a `ReevalOutcome`:
- `NO_CHANGE` — lease still valid, same context
- `EXTEND` — lease extended with same heuristic
- `SWITCH` — different heuristic selected
- `PROPOSE_AI` — always fires when `ai_status == "available"`
- `NO_HEURISTIC` — no valid heuristic available

### DecisionTrace + DomainMetrics (`decision_trace.py`, `decision_trace_repo.py`)
Immutable per-decision audit records. Persisted to SQLite. `DomainMetrics` accumulates counters per surface (ai_success, ai_timeout, heuristic_fallback, etc.).

## Behavioral Patterns

### 1. Strategy Pattern (`strategy.py`)
`HeuristicStrategy` ABC with three concrete implementations:
- `DefaultTuiSnakeStrategy` — follow goal / lurk when no goal
- `DefaultEclipseSnakeStrategy` — panel-to-motion mapping
- `DefaultChatCodeCompassStrategy` — artifact → context_summary

`decide_for_context(ctx, candidates)` selects strategy by domain.

### 2. Chain of Responsibility (`chain.py`, `snake_rules.py`, `chat_selectors.py`)
`HeuristicRuleChainElement` sorted by priority. Returns `ChainResult` with status `handled | continue | abstain`.

Snake rules (priority order):
1. ArtifactHoverRule
2. DiffFocusRule
3. ChatFocusRule
4. ErrorFocusRule
5. IdleLurkRule
6. DefaultFollowRule (fallback)

Chat selectors (priority order):
1. SelectedArtifactSelector
2. ActiveGoalSelector
3. ErrorHelpcenterSelector
4. SymbolSelector
5. FileSelector
6. TodoSelector
7. SourcepackSelector
8. NoMatchSelector (fallback)

### 3. State Machine (`state_machine.py`)
`SnakeStateMachine` (7 states): Following → WaitingAi ← → FallbackActive → Lurking / Explaining / Chatting / Disabled

`ChatStateMachine` (7 states): WaitingAi → HeuristicContextSelection → HeuristicAnswerReady / AiAnswerReady / StaleAiAnswer / NoMatch / PolicyDenied

AI timeout = 2.5s. `InvalidTransitionError` on illegal state transitions.

### 4. Observer / EventBus (`event_bus.py`)
`HeuristicEventBus` with ring buffer (100 events). Adapters normalize surface-specific events:
- `TuiEventSourceAdapter` — maps TUI key/mouse events
- `EclipseEventSourceAdapter` — maps Java plugin events

### 5. Command Pattern (`heuristic_commands.py`)
`HeuristicCommand` ABC with `execute(adapter) → CommandResult`. Commands are unit-testable without UI dependencies.

Available commands: `FollowWithDistance`, `LurkNear`, `ShowHint`, `OpenChat`, `ShowContextSummary`, `OpenSourceRef`, `AskScope`, `NoAction`

## AI Evolution Pipeline

```
DecisionTrace (expired TTL / fallback)
      │
      ▼
ProposalService.generate_from_traces()     (T06.03)
      │  anonymised trace IDs only
      ▼
HeuristicProposal → heuristics/candidates/
      │
      ▼
ProposalReviewView (TUI)                   (T06.05)
      │  operator approve / reject / request_changes
      ▼
HeuristicProposalValidator + SimulationHarness   (T07.01, T07.02)
      │  validation + simulation must both pass
      ▼
HeuristicActivationGate.activate()         (T07.03)
      │  3 gates: validation + simulation + human_approval_ref
      ▼
heuristics/active/  ←  HeuristicRegistry.reload()
```

New heuristics are **never activated automatically**. The activation gate enforces: validation passed + simulation passed + human approval registered in the audit log.

## Worker Roles

| Worker | Role |
|--------|------|
| `ananta-worker` | Heuristic control: runtime decisions, TTL reevaluation, trace analysis, candidate proposals |
| `opencode` | Code implementation only: new heuristic code, tests, refactoring — **never** as heuristic_controller |

The `check_heuristic_routing()` function in `worker_routing_policy_utils.py` enforces this separation with reason_code `opencode_not_allowed_as_heuristic_controller`.

## Security Boundaries

- **Snake domains**: only `read_local_context`, `read_artifact_refs`, `read_active_task` capabilities; must be `deterministic=True`
- **Chat domain**: additionally allows `read_source_refs`, `write_local_notes`, `send_to_chat`
- **Notes**: always local-only; require explicit `notes_context_released=True` to include in AI context
- **Eclipse adapter**: only zone classification strings cross the Java↔Python boundary — no raw file content
- **HallucinationGuardrail**: validates source_refs, blocks concrete file/symbol refs in no_good_match answers, blocks sensitive content

## AI Snake Evolution Extensions (ASH-0xx)

Added as part of the heuristic evolution hardening track:

| Module | Added in |
|---|---|
| `governance.py` | ASH-004: GovernanceMode enum |
| `snake_interfaces.py` | ASH-001: MovementMode, SnakeRuntimeState, CandidateRecord, ActivationPolicy |
| `snake_state_catalog.py` | ASH-003: SnakeState enum + transition table |
| `candidate_raw_validator.py` | ASH-013: Raw data protection |
| `candidate_migration.py` | ASH-016: Migration gate for null simulation_result |
| `candidate_scorer.py` | ASH-021: CandidateScore |
| `shadow_runner.py` | ASH-020+023: ShadowRunner + Live Watchdog |
| `snake_simulation_fixtures.py` | ASH-022: Standard fixture library |
| `snake_audit_events.py` | ASH-033: Structured lifecycle events |
| `auto_activator.py` | ASH-030+032+034: AutoActivator, RolloutState, rollback |

See [ai-snake-heuristic-evolution.md](../operator-tui/ai-snake-heuristic-evolution.md) for the full lifecycle documentation.

---

## File Layout

```
agent/services/heuristic_runtime/
  heuristic_registry_service.py   — HeuristicRegistry, HeuristicDefinition
  decision_result.py              — DecisionResult (unified output model)
  decision_context.py             — DecisionContext (unified input model)
  decision_trace.py               — DecisionTrace, DomainMetrics
  lease_reevaluation_service.py   — LeaseReevaluationService, ReevalOutcome
  strategy.py                     — HeuristicStrategy, 3 implementations
  chain.py                        — RuleChain, HeuristicRuleChainElement
  snake_rules.py                  — 6 snake rules
  chat_selectors.py               — 8 chat selectors
  state_machine.py                — SnakeStateMachine, ChatStateMachine
  event_bus.py                    — HeuristicEventBus, adapters
  heuristic_commands.py           — HeuristicCommand, 3 adapters
  chat_query_classifier.py        — ChatQueryClassifier, IntentKind
  snake_decision_manager.py       — SnakeDecisionManager, LurkStateManager
  chat_decision_manager.py        — ChatDecisionManager
  chat_context_selector.py        — ChatContextSelector
  heuristic_selection_service.py  — AI/worker control path
  heuristic_tool.py               — HeuristicTool ("select_heuristic")
  proposal_validator.py           — HeuristicProposalValidator
  simulation_harness.py           — HeuristicSimulationHarness
  activation_gate.py              — HeuristicActivationGate
  proposal_service.py             — ProposalService
  eclipse_snake_adapter.py        — EclipseSnakeDecisionAdapter

agent/repositories/
  heuristic_lease_repo.py         — HeuristicLeaseRepository
  decision_trace_repo.py          — DecisionTraceRepository

client_surfaces/operator_tui/
  ai_snake_policy.py              — PolicyDecision (snake)
  chat_policy.py                  — check_policy, HallucinationGuardrail
  chat_state.py                   — ChatAnswerBlock, SourceRef
  heuristic_debug_view.py         — HeuristicDebugView, status bar
  proposal_review.py              — ProposalReviewView

heuristics/
  index.json                      — registry index
  active/                         — currently active heuristics
  archive/                        — previous versions
  candidates/                     — proposals awaiting review
  quarantine/                     — suspended heuristics
  rejected/                       — rejected proposals

schemas/heuristic/
  heuristic_decision_lease.v1.json
  heuristic_proposal.v1.json
  decision_result.v1.json

prompts/heuristic_evolution/
  improvement_prompt.j2           — Jinja2 template for AI proposal generation
```
