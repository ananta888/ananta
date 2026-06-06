# Agent Profile: new_software_project

## Scope

This profile applies when the active task mode, planning mode, goal template, or route is `new_software_project`.

Related current code and docs:

```text
config/planning_prompts.default.json
worker/core/template_propose_handler.py
PROMPT_ANANTA_3ER_LAUF_NEW_SOFTWARE_PROJECT_OLLAMA.md
docs/goal-input-schemas.md
docs/golden-path-cli.md
docs/architecture/planning-template-inventory.md
```

This profile does not apply to pure AI-Snake-Chat explanation questions unless the user explicitly asks to create or implement a new project.

## Role

For `new_software_project`, the agent acts as a bounded software project architect and implementation worker inside Ananta.

It should:

- clarify the project goal only when required
- create a small executable project structure
- produce concrete artifacts, not generic advice
- work through Hub/Task/Worker control flow
- prefer small verified slices over large speculative generation
- record outputs, commands, files, tests, and review notes as artifacts or task state

## Existing Behavior To Preserve

The current planning prompt already requires setup, implementation, execution, verification, and summary/review coverage. It also requires concrete outputs and structured task lists.

The deterministic baseline in `worker/core/template_propose_handler.py` currently creates a minimal project directory with `README.md` and `main.py` and returns expected artifacts.

Those behaviors are valid baselines, but they are not enough for richer project generation. Richer behavior must still preserve deterministic fallback behavior and structured output contracts.

## Context And Input Rules

A `new_software_project` task may use:

- user goal text
- selected blueprint or template
- planning prompt version
- task state
- prior artifacts
- approved source/reference files
- CodeCompass candidates if the project is based on an existing repository or example
- deterministic tool outputs

CodeCompass may suggest relevant templates, existing examples, or reference files. It must not replace the original file content when precise implementation details are needed.

When data is missing, the worker should request additional context through the Hub instead of guessing.

Expected context request shape:

```json
{
  "task_id": "...",
  "mode": "new_software_project",
  "reason": "Need existing template/example/config before proposing next step",
  "requested_paths": [],
  "requested_topics": [],
  "graph_expansion": false
}
```

The Hub decides what files, chunks, artifacts, or denials are returned.

## Propose / Execute Meaning

For this profile, `propose` means:

```text
propose the next executable project step from current task state, approved context, and previous artifacts
```

It should not be just a one-shot LLM response.

Expected loop:

```text
Task exists
  -> Hub builds current context bundle
  -> Worker propose returns one concrete next step
  -> Hub checks policy and user intent
  -> execute runs the approved step
  -> outputs, read files, generated files, commands, and test results are stored
  -> next propose continues from that accumulated state
```

A proposal should clearly say whether it is:

- executable now
- blocked by missing context
- needs user review
- advisory only

## Output Rules

Every executable step should name at least one concrete output:

```text
file path
folder path
command
endpoint
artifact id
test result
review document
```

Avoid vague tasks like:

```text
Setup
Implement project
Finalize
Improve quality
```

Prefer concrete tasks like:

```text
Create <project>/README.md
Create <project>/src/main.py
Add pytest test for CLI output
Run python -m pytest
Write artifact summary with changed files
```

## Safety And Boundaries

The worker must not:

- bypass the Hub
- silently access files outside approved scope
- run unsafe system commands
- install global dependencies without policy approval
- treat generated code as verified before tests run
- continue indefinitely without task-state checkpoints

The worker should prefer:

- local project folders
- explicit file writes
- reproducible commands
- small commits/patches
- test-first or test-near workflows where practical
- reviewable artifact summaries

## Relationship To AI-Snake-Chat

AI-Snake-Chat may start or explain a `new_software_project` flow, but it should not itself become the implementation worker.

Correct separation:

```text
Snake-Chat
  -> explains the goal, asks Hub to create/select task, shows progress

new_software_project worker/profile
  -> plans, proposes, executes, verifies, records artifacts
```

## Acceptance Criteria For Implementations

Changes to this path are acceptable when:

- `new_software_project` planning still returns structured tasks.
- Generated tasks include setup, analysis/design, coding, testing, and review/handoff when relevant.
- Each task has a concrete output target.
- `propose` can return executable, blocked, needs-review, or advisory status.
- `execute` records generated files, commands, test output, and artifacts in task state.
- A second `propose` can continue from the first `execute` result.
- Missing files/context are requested through the Hub, not guessed.
- Deterministic fallback behavior from `TemplateProposeHandler` remains tested.
- Existing planning prompt tests and golden-path CLI tests still pass.

## Suggested Tests

Add or maintain tests for:

- mode detection for `new_software_project`
- planning prompt contract fields
- deterministic template proposal output
- expected artifact paths
- propose -> execute -> propose continuation
- context request when template/example data is missing
- no direct file access outside approved task scope
- compatibility with existing `PROMPT_ANANTA_3ER_LAUF_NEW_SOFTWARE_PROJECT_OLLAMA.md` flow
