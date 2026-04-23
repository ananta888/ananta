# ContextManager Target Model

## Goal

Establish one shared context assembly model for task execution, research and future repair flows.

## Core sources

ContextManager assembles context from:

1. Result memory (recent execution outcomes and artifacts)
2. Task neighborhood (parent/child/sibling task signals)
3. Repository retrieval (code/docs lookup)
4. Research artifacts (structured findings and citations)
5. Future wiki/knowledge sources (tenant-scoped knowledge documents)

## Assembly pipeline

1. Collect candidate context blocks from all enabled sources.
2. Normalize metadata (`source_type`, `priority`, `token_estimate`, `provenance`).
3. Score by relevance + policy priority.
4. Enforce token budget by source class and global ceiling.
5. Compact low-priority/older blocks into bounded summaries.
6. Emit final bundle with explicit provenance map.

## Budget model

- Global budget: `context_budget_total_tokens`
- Source reserves: per source/type quotas (for example repo vs memory vs artifacts)
- Priority reserve: keep minimum budget for high-priority control signals
- Overflow handling: compaction first, truncation last

## Priority model

Priority tiers:

- `P0 control`: task objective, acceptance criteria, policy constraints
- `P1 execution memory`: latest results, failure reasons, loop/approval signals
- `P2 retrieval context`: relevant code/docs snippets
- `P3 extended background`: older or low-confidence context

## Compaction rules

- Compaction must preserve provenance (`origin_id`, source, timestamp).
- Critical control state is never silently dropped.
- Compaction output is reusable across multi-turn sessions.

## Observability output

Each bundle exposes:

- budget requested vs used
- source contribution breakdown
- compaction actions taken
- dropped/trimmed items with reason codes
