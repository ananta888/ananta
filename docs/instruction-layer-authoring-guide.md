# Instruction Layer Authoring Guide

This guide explains how to write safe and useful `user_profile` and `task_overlay` prompts.

## Profile vs. overlay

- Use a **user profile** for persistent working style.
- Use a **task overlay** for temporary task/goal/session-specific focus.
- Keep both scoped to style and execution expression, not policy control.

## Safe profile patterns

- "Keep explanations concise and implementation-first."
- "Prefer German output and explicit trade-off notes."
- "Start with review findings, then propose minimal safe edits."

## Safe overlay patterns

- "For this task, prioritize tests before refactoring."
- "For this session, provide detailed acceptance-criteria coverage."
- "For this goal, focus on migration compatibility checks."

## Anti-patterns (blocked)

- Any instruction to bypass governance, policy, approval, or safety checks.
- Any directive to request unrestricted tool/runtime/filesystem access.
- Metadata that tries to override approval/security/tool policies.

Conflicts are rejected with `instruction_policy_conflict`.

## Preset examples

The backend exposes safe starter profiles via:

- `GET /instruction-profiles/examples`

Current examples:

1. `concise-coding`
2. `research-helper`
3. `review-first`
