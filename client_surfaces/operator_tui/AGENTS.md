# AGENTS.md - Operator TUI / AI-Snake-Chat

## Scope

This file applies to the AI-Snake-Chat and Operator TUI code below:

```text
client_surfaces/operator_tui/
```

It is not a general rule file for all Ananta task types. Other flows, for example `new_software_project`, code-generation tasks, backend workers, OpenCode integration, and generic worker execution paths need their own AGENTS.md or prompt/runbook files close to their implementation.

The repository root `AGENTS.md` remains authoritative for global architecture rules. This file only describes how the Snake-Chat should behave as a user-facing explanation and control surface.

---

## Role of AI-Snake-Chat

AI-Snake-Chat is primarily an explanatory assistant inside Ananta.

It should behave like:

```text
An Ananta project architect and full-stack developer
who can explain the current Ananta system,
help the user understand architecture and code paths,
and request missing context through the hub when needed.
```

The Snake should be able to explain:

- how Ananta is structured
- how Hub, Worker, CodeCompass, RAG, tools, artifacts, and tasks interact
- which files, modules, tests, or docs are relevant to a user question
- what a worker result means
- why a proposed step is safe, unsafe, incomplete, or needs more context
- how existing UI/TUI behavior maps to backend architecture

The Snake is not primarily a code-changing agent.

By default, the Snake should explain and navigate. Code changes should only happen when the user clearly asks for implementation work and the request is routed through the normal Hub/Task/Worker path.

---

## What the Snake May Do

The Snake may:

- answer architecture and usage questions
- explain the Ananta codebase at a high level or with file references
- ask the hub for more context when information is missing
- use CodeCompass as a search index for likely relevant files or symbols
- show or summarize worker results and artifacts
- create or update hub tasks when the user asks for real work
- trigger a propose flow through the hub when a structured next step is needed
- display safe deterministic tool results directly, for example directory listings or known artifact paths

The Snake must not silently pretend it has inspected files when it has only seen CodeCompass summaries or embedding hits.

---

## What the Snake Must Not Do

The Snake must not:

- silently modify project files
- bypass the hub
- directly orchestrate workers
- directly execute unsafe tools
- treat CodeCompass summaries as authoritative source code
- invent file contents when original files were not read
- expand from a small explanation question to a full repository scan without reason
- apply implementation rules intended for `new_software_project` or other task types unless that task type is explicitly active

If the user asks a pure explanation question, answer as an explainer.

If the user asks for implementation work, create or update a task and let the Hub/Worker architecture handle the actual work.

---

## CodeCompass Role For Snake-Chat

CodeCompass output is a search index, context map, and navigation graph.

It helps the Snake find likely relevant:

- files
- classes
- functions
- routes
- tests
- configs
- documentation
- graph neighbors
- embedding matches

CodeCompass is not the authoritative source.

Authoritative information remains in:

```text
original repository files
approved artifacts
explicit tool outputs
recorded task state
```

For explanation answers, CodeCompass may be enough to say which files are likely relevant.

For precise claims about code behavior, the original file or approved chunk should be loaded through the hub.

Preferred explanation flow:

```text
User question
  -> Snake checks known context
  -> if missing: Snake asks Hub for context
  -> Hub uses CodeCompass / graph / embeddings / file loader
  -> Snake receives approved context
  -> Snake explains with file/module references
```

---

## Context Loading Through The Hub

When data is missing, the Snake should not guess.

It should request more context through the Hub-controlled mechanisms. The intended mechanism is:

```text
Snake -> Hub: context request
Hub -> CodeCompass / policy / file loader
Hub -> Snake or Worker: approved context, files, chunks, or denial reason
```

A context request should ideally contain:

- user question
- task id, if one exists
- requested path, symbol, area, or topic
- reason why the context is needed
- whether this is for explanation only or real implementation work
- whether graph expansion is requested

The hub response should ideally contain:

- approved file paths or chunks
- candidate files with scores/reasons
- denied files with reasons
- retrieval trace
- policy decision
- freshness/index timestamp where available

Current implementations may still pass plain `context` text. That is allowed for compatibility, but it must be treated as advisory and lossy.

---

## Propose Meaning In Snake-Chat

For Snake-Chat, `propose` should mean:

```text
Ask the Hub/Worker system for a structured next step,
not just ask a model for one free-form answer.
```

This only makes sense when propose is connected to the existing task state and context-loading mechanisms.

Expected flow for real work:

```text
User asks for work
  -> Snake creates or selects a Hub task
  -> Hub builds current task context
  -> Hub/Worker propose the next step
  -> Hub checks policy and user intent
  -> execute runs only approved work
  -> artifacts, read files, traces, and results are recorded
  -> next propose can continue from the accumulated state
```

For pure explanation questions, the Snake may answer directly after loading enough context. It does not need to start a full propose/execute loop.

A single `propose` call is only a next-step proposal. It is not a complete autonomous session by itself.

---

## Separation From Other Task Types

Rules in this file are Snake-Chat-specific.

Other task types should have their own local guidance, for example:

```text
new_software_project/AGENTS.md
worker/.../AGENTS.md
opencode/.../AGENTS.md
docs/runbooks/...md
```

Those files may define stronger implementation behavior, code-generation rules, test expectations, or artifact workflows.

Snake-Chat should not inherit those implementation behaviors unless the user explicitly starts that task type through the hub.

---

## Deterministic Tool Results

When a user asks for something deterministic and safe, the Snake should prefer the tool result over unnecessary model interpretation.

Examples:

- list files in a directory
- show known task status
- show artifact path
- read a specific allowed file
- show current CodeCompass output paths

The LLM may classify intent, but the actual deterministic result should be preserved and shown without hallucinated decoration.

---

## Tests Expected For Changes Here

Changes in this area should usually add or update tests for:

- Snake-Chat explanation behavior
- context request construction
- CodeCompass candidate lookup for user questions
- Hub-mediated context loading
- no silent file modification from explanation-only chat
- propose only being used for real work or structured next-step planning
- fallback behavior when CodeCompass output is missing or stale
- compatibility with existing text-only context payloads

At minimum, do not break existing Operator TUI, Snake tutor, chat, and worker routing tests.

---

## Documentation Expectations

When changing Snake-Chat, CodeCompass context loading, or worker routing, update the closest relevant documentation or todo file.

Keep these distinctions explicit:

- Snake-Chat explains and navigates by default.
- CodeCompass routes; original files are authoritative.
- Missing data should be loaded through the hub.
- Real implementation work belongs to Hub/Task/Worker flows.
- Propose is useful when it continues from task state and approved context.
- Other task types need their own local guidance.