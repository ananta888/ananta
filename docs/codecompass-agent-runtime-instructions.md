# CodeCompass Agent Runtime Instructions

This document is the authoritative reference for how Ananta agents (ananta-worker,
OpenCode, AI-Snake-Chat) treat CodeCompass-derived context. It pairs with the
context-trust model in `docs/security/codecompass-context-trust-model.md` and
the reload-request contract in `docs/contracts/codecompass-context-reload-request.md`.

The instructions in this document are enforced by the `codecompass_runtime`
layer in `InstructionLayerCompiler` (see `agent/services/instruction_layer_compiler.py`).
That layer is **non-overridable**: user profiles, task overlays, and goal overlays
cannot remove or weaken any of the rules below. Attempts to do so are silently
rejected and audit-logged as `codecompass_runtime_override_rejected`.

## Context types

CodeCompass delivers the following kinds of context. Agents must read each
type as the kind of evidence it actually is, and nothing more.

| Type | Meaning | Evidence? |
|---|---|---|
| `chunk` | A small unit of repository text (file path + range + content) | weak — by itself, just text |
| `file_excerpt` | A file slice with a declared line range | weak — a snippet is not a contract |
| `line_range` | Just a (path, start, end) triple; no content | weak — pointer only |
| `codecompass_snippet` | An extracted snippet from the CodeCompass pipeline, tagged with `source_kind: codecompass_snippet` | weak — indexed hint |
| `hub_context` | A bundle assembled by the Hub from one or more of the above | weak — composed from weak parts |
| `node` | A graph node: type, file, name, record_id, kind, role_labels | medium — a node is a typed pointer |
| `edge` | A graph edge with `edge_type`, `source_id`, `target_id`, `confidence` | medium — typed relation |
| `score` | A numeric ranking, always paired with the path it ranks | informational — never the answer |
| `warning` | A textual caveat attached to a result (e.g. `calls_probable_target edges are heuristic`) | strong — read before answering |
| `evidence_path` | A list of edges with `edge_type`, `direction_used`, `confidence` | strong — a chain of typed relations |
| `architecture_query_result` | A typed query result (`dto-impact`, `controller-test-coverage`, `field-policy-impact`, `service-dependency-chain`) with `enforcement`, `operations`, evidence paths, and warnings | strong when it carries `enforcement: enforced_backend_guard`; reference-only when it carries `enforcement: frontend_reference` or `weak_reference` |

**Hard distinction:** hard evidence (a typed edge or a backend-enforcement
result) and heuristic hints (name-only matches, FTS fallbacks, frontend-only
guards) must never be conflated. The `warning` field is part of the answer
logics, not noise to filter.

## Reading rules

1. Treat CodeCompass context as **indexed repository hints with evidence**, not
   as complete truth. The graph was built by extractors that may miss code,
   especially dynamic-language and bytecode paths.
2. **Never raten.** If a field's policy impact, a controller's test coverage,
   or a service's dependency is unclear from the supplied context, say so.
3. **Benenne fehlende Daten** explicitly. "Not in the supplied context" is a
   valid answer; "probably no protection" is not.
4. **Fordere Nachladen gezielt an** when you need more data. Use a
   `context_reload_request` (see the contract document). Do not browse the
   repository on your own.
5. **Behaupte keine Coverage, Policy-Wirkung oder Dependency ohne
   Evidence-Pfad.** A name match in the graph is not coverage. A frontend
   guard reference is not backend enforcement.
6. **Surface warnings, do not filter them.** Heuristic evidence comes with a
   warning; the warning is the difference between "we have evidence" and
   "we have a hint."

## Canonical prompt block

The following block is the canonical runtime instruction. It is prepended to
the agent's prompt by the `codecompass_runtime` layer whenever CodeCompass
context is present or the agent template is one of the supported kinds
(`opencode`, `ananta_worker`, `ai_snake_chat`).

```
Du bekommst CodeCompass-Kontext. Behandle ihn als indexierte Repo-Hinweise
mit Evidence, nicht als vollstaendige Wahrheit. Nutze Pfade, Line-Ranges,
Scores, Nodes, Edges und Warnings. Wenn relevante Daten fehlen, rate nicht:
benenne den fehlenden Kontext und fordere gezielt ueber den Hub Nachladen an.
Behaupte keine Coverage, Policy-Wirkung oder Dependency ohne Evidence-Pfad.
```

This block is intentionally short. The detailed rules above are the
authoritative reference; the block is the prompt-time reminder.

## Reload request

When an agent determines that the supplied context is insufficient, it issues
a `context_reload_request` to the Hub. The wire format, validation rules, and
limits are defined in
[`docs/contracts/codecompass-context-reload-request.md`](../contracts/codecompass-context-reload-request.md).

The Hub validates the request, deduplicates and limits the entries, and serves
them through the existing `ContextDeliveryService`. Any request that is not
`risk: read_only` is rejected with `policy_blocked`.

## AI-Snake-Chat specifics

When CodeCompass context is shown inside AI-Snake-Chat:

- The chat UI shows a "nachladen empfohlen" marker when the model answer
  cites no evidence path for a security-relevant claim (e.g. "is field X
  protected?").
- The chat issues a `context_reload_request` to the Hub when the user asks
  a follow-up that requires additional context the model did not have when
  answering the previous turn.
- The chat does **not** claim completeness on missing evidence. Empty
  CodeCompass results are rendered as "CodeCompass hat zu dieser Frage
  nichts Belegbares gefunden" — never as a "no, it is not protected"-style
  negative claim.

## Layer activation

The `codecompass_runtime` layer is activated when **any** of the following
holds:

- The environment variable `ANANTA_CODECOMPASS_RUNTIME_LAYER_ENABLED` is set
  to a truthy value (`1`, `true`, `yes`).
- The task payload carries a `codecompass_context` block.
- The task's `agent_template` is one of: `opencode`, `ananta_worker`, `ai_snake_chat`.

When the layer is active, its source is `hub_policy` and `overridable: false`.
The compiler rejects any overlay entry that tries to add a layer with the
same `id` and a different `source`.
