# AGENTS.md - Operator TUI / AI-Snake-Chat

## Scope

This file applies to everything below:

```text
client_surfaces/operator_tui/
```

It specializes the repository-wide `AGENTS.md` rules for the Operator TUI, AI-Snake-Chat, Snake tutor flows, CodeCompass-assisted context lookup, and worker handoff behavior.

The root `AGENTS.md` remains authoritative. If this file conflicts with the root rules, the root rules win.

---

## Core Rule

AI-Snake-Chat is a user-facing control and explanation surface.

It must not become an uncontrolled autonomous worker.

The Snake may:

- explain the current UI state
- answer user questions
- request context through the hub
- create or update hub tasks
- ask the hub to route work to workers
- display worker results and artifacts

The Snake must not:

- bypass the hub
- directly orchestrate workers
- directly execute unsafe tools
- silently modify project files
- treat CodeCompass summaries as authoritative source code
- invent file contents when original files were not read

---

## Hub / Worker Boundary

All orchestration flows through the hub.

The Snake may initiate a request, but the hub owns:

- task creation
- task state
- worker routing
- policy enforcement
- context approval
- file access decisions
- propose/execute loop control
- artifact registration

Workers execute delegated work only.

Workers must not directly ask other workers for context or tasks. If a worker needs more context, it must request it through the hub.

---

## CodeCompass Role

CodeCompass output is a search index and navigation graph.

It may be used to find likely relevant:

- files
- symbols
- classes
- functions
- tests
- documentation
- config files
- graph neighbors
- embedding matches

CodeCompass output is not the authoritative project source.

Authoritative information remains in the original repository files and approved artifacts.

For non-trivial answers or modifications, the flow must be:

```text
User question
  -> CodeCompass / graph / embeddings find candidates
  -> hub builds CandidateFiles / ContextBundle
  -> worker reads original files or approved chunks
  -> worker answers or proposes changes based on those sources
```

Do not answer as if a file was inspected when only `index.jsonl`, `embedding.jsonl`, `graph_nodes.jsonl`, `graph_edges.jsonl`, or `relations.jsonl` was inspected.

---

## AI-Snake-Chat Context Handoff

The Snake-Chat path must prefer structured context handoff over plain prompt stuffing.

Preferred future contract:

```json
{
  "question": "...",
  "context": "short human-readable context",
  "candidate_files": [
    {
      "path": "...",
      "score": 0.0,
      "reason": "...",
      "source": "codecompass|embedding|graph|manual|task_state"
    }
  ],
  "context_files": [
    {
      "path": "...",
      "line_start": 1,
      "line_end": 120,
      "content_ref": "...",
      "authoritative": true
    }
  ],
  "memory_context": {},
  "policy": {
    "answer_from_original_files": true,
    "allow_worker_context_requests": true
  }
}
```

Until the full contract exists, any text-only context must be treated as advisory and lossy.

---

## Propose / Execute Behavior

Snake-initiated work should use the existing task/propose/execute model whenever real project work is requested.

Expected loop:

```text
Snake request
  -> hub task
  -> propose next step
  -> policy check
  -> execute approved step
  -> record artifacts, read files, traces, worker requests
  -> next propose step reuses accumulated task state
```

A single `propose` call is only a next-step proposal. It is not a complete autonomous session by itself.

The hub must remain responsible for loop boundaries such as:

- done
- blocked
- needs_user_review
- needs_more_context
- max_steps
- policy_denied

---

## Worker Context Requests

Workers may need additional files after the first handoff.

That must happen through a hub-controlled request, not by uncontrolled direct repository access.

Allowed pattern:

```text
Worker -> hub: request more context
Hub -> CodeCompass / policy / file loader
Hub -> worker: approved ContextFiles or denial reason
```

Context requests should include:

- requested path or symbol
- reason
- required action
- expected use
- current task id
- whether graph expansion is requested

The hub response should include:

- approved files or chunks
- denied files with reasons
- retrieval trace
- policy decision
- source freshness / index timestamp where available

---

## Safety and Least Privilege

Default behavior must be least privilege.

The Snake must not silently expand context scope from a small question to the whole repository.

Preferred order:

1. exact file or symbol match
2. nearby graph neighbors
3. tests and configs linked to the match
4. broader semantic retrieval
5. full repository scan only when explicitly justified

Secrets, credentials, private keys, tokens, `.env` files, and generated sensitive artifacts must not be forwarded to workers unless an explicit policy allows it.

---

## Deterministic Tool Use

When a user request is deterministic and safely answerable by a tool, the system should prefer the tool result over an unnecessary LLM answer.

Examples:

- list files in a directory
- show known task status
- show known artifact path
- read a specific allowed file
- show current CodeCompass output paths

The LLM may decide intent, but deterministic execution results should not be paraphrased into unreliable guesses.

---

## Tests Expected For Changes Here

Changes in this area should usually add or update tests for:

- Snake-Chat request payload construction
- CodeCompass candidate file selection
- ContextBundle creation
- worker context handoff compatibility
- worker context request roundtrip through the hub
- propose/execute loop state persistence
- policy denial for unsafe file access
- fallback behavior when CodeCompass output is missing or stale

At minimum, do not break existing Operator TUI, Snake tutor, chat, and worker routing tests.

---

## Documentation Expectations

When changing Snake-Chat, CodeCompass context handoff, or worker routing, update the closest relevant documentation or todo file.

Important concepts to keep explicit:

- CodeCompass routes; original files are authoritative.
- The hub orchestrates; workers execute.
- The Snake is a UI/control surface, not an uncontrolled autonomous worker.
- Propose/execute is a step loop, not a single magic LLM call.
- Context expansion must be auditable and policy-controlled.
