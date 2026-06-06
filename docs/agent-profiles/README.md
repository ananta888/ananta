# Agent Profiles / Local AGENTS Guidance

## Purpose

Ananta uses different interaction paths for different kinds of work. A single global agent role is too vague and too dangerous.

Use this directory to document task-specific agent behavior in a way that stays general enough to be reusable, but concrete enough for the active path.

The root `AGENTS.md` remains the global architecture and repository rule file. Local `AGENTS.md` files narrow those rules for a specific surface or task type.

## Profile Map

The current standard-path mapping is documented in:

```text
docs/agent-profiles/profile-map.json
```

This map is documentation-first today and should become the input for a runtime `AgentProfileLoader` later. The intended runtime order is:

```text
root AGENTS.md
  + active path AGENTS.md
  + task/context bundle
  + response contract
```

## Current Profile Types

| Profile | Primary use | Main behavior |
| --- | --- | --- |
| `client_surfaces/operator_tui/AGENTS.md` | AI-Snake-Chat / Operator TUI | Explain, navigate, request context through Hub, do not silently modify code |
| `docs/agent-profiles/new_software_project/AGENTS.md` | `new_software_project` goals | Plan and build a new project in small verified slices through Hub/Task/Worker |
| `docs/agent-profiles/feature/AGENTS.md` | `feature` goals | Implement one bounded capability slice |
| `docs/agent-profiles/bug_fix/AGENTS.md` | `bug_fix` goals | Reproduce, diagnose, patch, verify |
| `docs/agent-profiles/code_fix/AGENTS.md` | `code_fix` goals | Apply a minimal bounded code patch |
| `docs/agent-profiles/refactor/AGENTS.md` | `refactor` goals | Preserve behavior while improving structure |
| `docs/agent-profiles/test/AGENTS.md` | `test` goals | Add or run focused verification |
| `docs/agent-profiles/tdd/AGENTS.md` | `tdd` goals | Red-green-refactor, test first |
| `docs/agent-profiles/repo_analysis/AGENTS.md` | `repo_analysis` goals | Explain repository structure from evidence |
| `docs/agent-profiles/sys_diag/AGENTS.md` | `sys_diag` goals | Diagnose with bounded, low-impact checks |
| `docs/agent-profiles/admin_repair/AGENTS.md` | `admin_repair` goals | Dry-run-first bounded repair planning |
| `docs/agent-profiles/incident/AGENTS.md` | `incident` goals | Triage and mitigate with evidence preservation |
| `docs/agent-profiles/architecture_review/AGENTS.md` | `architecture_review` goals | Review architecture without silent implementation |
| `docs/agent-profiles/project_evolution/AGENTS.md` | `project_evolution` goals | Extend existing systems incrementally |

Legacy note: `docs/agent-profiles/new-software-project.md` existed before the per-path `AGENTS.md` layout and should be treated as background documentation until consolidated.

## Profile Boundary Rule

A profile applies only when its path or task type is active.

Examples:

```text
AI-Snake-Chat question
  -> use Operator TUI / Snake-Chat guidance

new_software_project task
  -> use docs/agent-profiles/new_software_project/AGENTS.md

bug_fix task
  -> use docs/agent-profiles/bug_fix/AGENTS.md
```

Profiles must not leak into unrelated flows. The Snake-Chat should not behave like an implementation worker unless the user explicitly starts implementation work through the Hub. A `new_software_project` worker should not behave like a chat explainer unless the task is only analysis or review.

## Common Contract

Every profile should define:

- scope and activation condition
- role of the agent in that path
- what may be done directly
- what must go through the Hub
- context-loading rules
- how CodeCompass/RAG may be used
- expected propose/execute behavior
- artifact and verification expectations
- tests that should protect this behavior

## Context Rule

CodeCompass, graph nodes, graph edges, embeddings, and summaries are routing aids.

Authoritative information comes from:

```text
original files
approved context chunks
approved artifacts
explicit tool outputs
task state
```

If important data is missing, the active profile should request it through the Hub instead of guessing.

## Propose Rule

`propose` should not mean "ask an LLM for a random answer".

For implementation paths, `propose` means:

```text
build the next structured step from task state, approved context, available artifacts, and current policy
```

For explanation paths, `propose` is optional and should only be used when a structured next step or task handoff is needed.

## Runtime Gap

The profile files are now present and mapped, but code still needs a loader that attaches the active profile to OpenCode/ananta-worker handoffs.

Required follow-up:

```text
AgentProfileLoader
  -> resolve profile by task mode/template/path
  -> read root AGENTS.md
  -> read active profile AGENTS.md
  -> compose worker workspace AGENTS.md or prompt context
```
