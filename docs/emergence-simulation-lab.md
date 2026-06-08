# Emergence Simulation Lab

A deterministic, multi-agent social simulation framework built into Ananta.
Agents make decisions via pluggable LLM adapters; world state evolves through
a strict policy engine with no hidden side-effects.

## Design Goals

- **No real-world side effects** — simulation actions never touch the filesystem, network, or shell
- **Deterministic by default** — given the same seed + scripted adapter, runs reproduce exactly
- **LLM-agnostic** — any provider (Ollama, OpenRouter, or the built-in DummyAdapter for CI)
- **Observable** — every tick hashes the world state; checkpoints and event logs are written to disk

## Package Structure

```
simulation/
  models/
    scenario.py       SIM-002  ScenarioConfig, BudgetConfig, LawDefinition
    world_state.py    SIM-003  WorldState + RelationshipGraph
    agent_profile.py  SIM-004  AgentProfile + AgentProfileLoader
    action.py         SIM-005/006  ActionProposal + typed arg schemas
    memory.py         SIM-014  AgentMemory + MemoryStore
  policies/
    policy_engine.py  SIM-007  Deterministic law + physics validation
    crime_model.py    SIM-009  CrimeLedger + CrimeConsequenceSystem
    governance.py     SIM-010  GovernanceProposal + voting
  engine/
    survival.py       SIM-008  Hunger/health/death tick model
    economy.py        SIM-012  MarketSystem + ResourceRegenSystem
    prompt_renderer.py SIM-013  Per-agent LLM prompt assembly
    budget_guard.py   SIM-020  Resource limit enforcement
    tick_runner.py    SIM-021  Single-tick orchestration
    checkpoint.py     SIM-022  Snapshot save/load
    replay.py         SIM-023  Deterministic replay from trace
    run_artifact.py   SIM-024  On-disk directory layout
    batch_runner.py   SIM-031  Multi-scenario runner
    prompt_experiments.py SIM-032  Intervention / ablation experiments
  adapters/
    base.py           SIM-015  SimulationModelAdapter protocol
    dummy.py          SIM-016  DummyModelAdapter + ScriptedAdapter
    ollama.py         SIM-017  Local Ollama adapter
    openrouter.py     SIM-018  OpenRouter.ai adapter
    model_strategy.py SIM-019  Per-agent adapter resolver
    langgraph_runner.py SIM-039  LangGraph node wrapper (optional)
    n8n_wrapper.py    SIM-040  n8n webhook trigger (optional)
  metrics/
    core_metrics.py   SIM-025  Per-tick TickSnapshot + MetricsCollector
    failure_classifier.py SIM-026  Outcome classification
    report_generator.py SIM-027  JSON run report
    codecompass_integration.py SIM-036  RAG indexing for run artifacts
  scenarios/
    standard_scenarios.py SIM-030  3 built-in scenarios
    scenario_importer.py SIM-028/029  Import + attribution
  security/
    boundary.py       SIM-033  Real vs simulated tool boundary
    capability_model.py SIM-034  Per-role action allowlist
    prompt_injection.py SIM-035  Injection pattern scanner
  cli/
    commands.py       SIM-037  :sim TUI commands
```

## Quick Start

```python
from simulation.engine.batch_runner import BatchRunner
from simulation.scenarios.standard_scenarios import get_scenario
from simulation.models.scenario import BudgetConfig

scenario = get_scenario("survival_island").model_copy(
    update={"budget": BudgetConfig(max_ticks=20)}
)
results = BatchRunner().run([scenario])
print(results[0].report["outcome"])
```

## TUI Commands

```
:sim list                  # list available scenarios
:sim run survival_island   # run with default 10 ticks
:sim run governance_experiment --ticks 30
:help sim                  # show help
```

## Built-in Scenarios

| Name | Agents | Premise |
|------|--------|---------|
| `survival_island` | 3 | Castaways; scarce food; starvation pressure |
| `governance_experiment` | 5 | Town with laws + voting; crime enforcement |
| `trade_network` | 5 | 3 locations; barter economy |

## Tick Pipeline

```
ResourceRegenSystem.tick()
  ↓
for each living agent:
  PromptRenderer.render() → AdapterResponse → ActionProposal
  PolicyEngine.validate()  → allowed / denied / crime / noop
  PolicyEngine.apply()     → mutate WorldState + log SimEvent
  ↓
GovernanceSystem.tick()    → resolve/expire proposals
SurvivalSystem.tick()      → hunger/health decay → death events
MemoryStore.flush_all()    → perception → short_term
WorldState.advance_tick()
BudgetGuard.record_tick()  → check limits
```

## Security Boundaries

- `SimulationSecurityBoundary` blocks any action in `_REAL_WORLD_RISK_ACTIONS` unconditionally
- `PromptInjectionGuard` scans LLM output for injection patterns before parsing
- `AgentCapabilityModel` enforces per-role action allowlists
- Adapter errors produce `ActionProposal.invalid_fallback()` (noop), never crash the tick

## Adding a New Scenario

```python
from simulation.models.scenario import ScenarioConfig, BudgetConfig, LawDefinition

my_scenario = ScenarioConfig(
    name="my_experiment",
    agents=[{"id": "a1", "name": "Alice", "role": "citizen", "location_id": "town"}],
    locations=[{"id": "town", "name": "Town"}],
    laws=[LawDefinition(id="no_attack", description="No violence",
                         forbidden_actions=["attack"], penalty="imprisonment")],
    budget=BudgetConfig(max_ticks=50),
)
```

## Deterministic Replay

```python
from simulation.engine.replay import ReplayTrace, ReplayRunner
from simulation.engine.checkpoint import CheckpointManager

# Record a run
trace = ReplayTrace()
# ... run and record each TickResult ...
trace.save("replay_trace.json")

# Replay from checkpoint
cm = CheckpointManager("runs/my_run/checkpoints")
initial = cm.load(cm.latest())
runner = ReplayRunner(initial, trace, scenario)
for result in runner.run():
    print(result.tick, result.state_hash)
```
