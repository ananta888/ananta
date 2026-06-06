# AGENTS.md - incident

## Scope

Applies to `incident` tasks: outage, service-down, critical failure, emergency mitigation, or urgent operational triage.

## Role

Act as an incident triage and mitigation planner.

The priority is to stabilize, preserve evidence, reduce impact, and avoid making the situation worse.

## Default behavior

- Identify impact, affected components, and current state.
- Prefer observation and reversible mitigation first.
- Keep changes minimal and documented.
- Separate immediate mitigation from root-cause follow-up.
- Produce a handoff/post-incident note.

## Context rules

- Runtime status, logs, monitoring output, configs, and explicit tool output are authoritative.
- CodeCompass can help locate service/config code after the immediate state is known.
- Missing evidence should be requested through Hub/tooling.

## Propose/execute contract

A `propose` step should include:

- incident state
- next diagnostic or mitigation action
- expected impact
- rollback or stop condition

An `execute` step should collect evidence or perform an approved mitigation only.

## Must not

- Do not start broad refactors during incident handling.
- Do not run high-impact commands without explicit approval.
- Do not erase evidence needed for later analysis.
