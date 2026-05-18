# Planning Pipeline

## Current default (Learning phase)

Ananta runs planning in an LLM-first learning mode by default.
Deterministic templates remain available, but they are selected by policy and evidence, not blindly as global default.

Flow:
Goal -> planning_queued -> planning_running -> planned/failed

## Safety boundary

LLM output may suggest task details, but policy-relevant decisions remain deterministic:
- no capability escalation from plan text
- no context scope escalation (for example forcing full/admin scope)
- no tool-permission activation from plan text

## Transition to deterministic-first

Deterministic-first is a later policy mode (`deterministic_first`) once metrics and review evidence are sufficient.
This transition is evidence-based and can be done per mode/model/profile.

## Related TODOs

- `todo.llm-first-planning-learning-and-response-behavior.json`
- `todo.planning-mechanism-hardening.json`
