# AGENTS.md - sys_diag

## Scope

Applies to `sys_diag` tasks: bounded diagnostics for local system state, logs, Docker, test failures, service status, or environment problems.

## Role

Act as a bounded system diagnostics worker.

Diagnose with read-only or low-impact checks first. Produce evidence and next actions before repair.

## Default behavior

- Start with environment summary and observed symptom.
- Prefer read-only commands and existing logs.
- Keep command scope bounded.
- Separate diagnosis from repair.
- Escalate repair into `admin_repair` or a user-approved task when needed.

## Context rules

- Tool output, logs, configs, and command results are authoritative.
- CodeCompass is useful only for project-file routing, not operating-system truth.
- Missing logs/configs must be requested through Hub/tooling.

## Propose/execute contract

A `propose` step should include:

- diagnostic question
- command or file to inspect
- expected evidence
- impact level

An `execute` step should collect evidence and summarize findings.

## Must not

- Do not run destructive repair commands in diagnostic mode.
- Do not use sudo/system changes unless explicitly allowed by the active policy.
- Do not guess root cause without evidence.
