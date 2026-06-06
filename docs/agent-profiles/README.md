# Agent Profiles / Local AGENTS Guidance

## Purpose

Ananta uses different interaction paths for different kinds of work. A single global agent role is too vague and too dangerous.

Use this directory to document task-specific agent behavior in a way that stays general enough to be reusable, but concrete enough for the active path.

The root `AGENTS.md` remains the global architecture and repository rule file. Local `AGENTS.md` files or profile documents narrow those rules for a specific surface or task type.

## Current Profile Types

| Profile | Primary use | Main behavior |
| --- | --- | --- |
| `client_surfaces/operator_tui/AGENTS.md` | AI-Snake-Chat / Operator TUI | Explain, navigate, request context through Hub, do not silently modify code |
| `docs/agent-profiles/new-software-project.md` | `new_software_project` goals | Plan and build a new project in small verified slices through Hub/Task/Worker |

## Profile Boundary Rule

A profile applies only when its path or task type is active.

Examples:

```text
AI-Snake-Chat question
  -> use Operator TUI / Snake-Chat guidance

new_software_project task
  -> use new-software-project guidance

bug_fix task
  -> use bug-fix guidance once added
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
