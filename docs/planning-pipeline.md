# Planning Pipeline

## Current default (Learning phase)

Ananta runs planning in an LLM-first learning mode by default.
Deterministic templates remain available, but they are selected by policy and evidence, not blindly as global default.

Flow:
Goal -> planning_queued -> planning_running -> planned/failed

Runner profile semantics:
- `small`: compact English planning prompts and tighter output/context limits for weaker local models.
- `medium`: moderate limits with English prompt defaults.
- `off`: no runner-injected planning overrides; hub defaults remain active (safe baseline mode, not a hard planning disable).

## Safety boundary

LLM output may suggest task details, but policy-relevant decisions remain deterministic:
- no capability escalation from plan text
- no context scope escalation (for example forcing full/admin scope)
- no tool-permission activation from plan text

## Planning track contract

Planning output is contract-first and grounded in `todos/todo.track.schema.json`.

Required core fields:
- `version`
- `owner`
- `track`
- `status_scale`
- `priority_scale`
- `risk_scale`
- `milestones`
- `tasks`
- `tasks_status_summary`

Optional extensions (allowed by design) include:
- `critical_path_tasks`
- `tasks_type_summary`
- `progress_summary`
- `execution_stage_summary`
- `summary_notes`
- `end_summaries`

## Transition to deterministic-first

Deterministic-first is a later policy mode (`deterministic_first`) once metrics and review evidence are sufficient.
This transition is evidence-based and can be done per mode/model/profile.

## Related TODOs

- `todo.llm-first-planning-learning-and-response-behavior.json`
- `todo.planning-mechanism-hardening.json`
