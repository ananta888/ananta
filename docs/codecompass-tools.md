# CodeCompass Tools (M3/M6 reference)

This document lists the public CodeCompass tool names exposed to the
ananta-worker tool loop. Names and JSON schemas are versioned and
frozen; new tools bump the major version.

## Schema Versioning

| Field          | Value                                  |
|----------------|----------------------------------------|
| Tool catalog   | `ananta_worker_tool_registry.v1`       |
| Result envelope| `ananta_tool_result.v1`                |

## Tool Registry (canonical names)

| Tool name                              | Source task | Description                                          |
|----------------------------------------|-------------|------------------------------------------------------|
| `codecompass.resolve_context`          | existing    | Resolve a context package; legacy flat result.       |
| `codecompass.search_symbols`           | existing    | Search symbol index.                                  |
| `codecompass.get_file_context`         | existing    | Read bounded file context.                            |
| `codecompass.get_domain_map`           | existing    | Domain map snapshot.                                  |
| `codecompass.search`                   | existing    | Combined retrieval search (hybrid contract).          |
| `codecompass.retrieve`                 | AHR-005     | Canonical agentic retrieval envelope.                 |
| `codecompass.architecture_overview`     | HAC-009     | Budgeted system/subsystem/component slice.            |
| `codecompass.architecture_expand`       | HAC-009     | Expand one architecture handle.                       |
| `codecompass.architecture_diagram`      | HAC-011     | Deterministic Mermaid view of the slice.              |
| `codecompass.layers_heads`              | CIL-022     | Read incremental layer heads.                         |
| `codecompass.layers_plan`               | CIL-022     | Dry-run incremental update plan.                      |
| `codecompass.analytics_query`           | DDB-031     | Named DuckDB analytics template, no free SQL.         |
| `codecompass.plan_context`             | existing    | Plan bounded path/range context.                      |
| `codecompass.expand_graph`             | existing    | Expand the symbolgraph around a seed.                 |
| `codecompass.architecture_query`       | existing    | Whitelisted architecture queries.                     |
| `codecompass.semantic_equivalents`     | existing    | Cross-language semantic equivalents.                  |
| `codecompass.translation_plan`         | existing    | Translation plan between language pairs.              |
| `codecompass.verify_translation`       | existing    | Verify a candidate translation.                       |
| `codecompass.python_translation_plan`  | existing    | Python-specific translation plan.                     |
| `codecompass.x86_overview`             | existing    | x86 overview.                                         |
| `codecompass.x86_address_lookup`       | existing    | x86 address lookup.                                   |
| `codecompass.x86_cfg`                  | existing    | x86 control-flow graph.                              |
| `codecompass.x86_call_graph`           | existing    | x86 call graph.                                       |
| `codecompass.x86_find`                 | existing    | Generic x86 finder.                                   |
| `codecompass.blast_radius`             | CRG-005     | Bounded blast radius over the symbolgraph.           |
| `codecompass.graph_metrics`            | CRG-007     | Hub / bridge metrics.                                 |
| `codecompass.knowledge_gaps`           | CRG-008     | Knowledge-gap analysis.                               |
| `codecompass.surprising_connections`   | CRG-009     | Surprising-connection candidates.                     |
| `codecompass.repository_query`         | RIG-005/006 | Whitelisted RIG query.                                |
| `codecompass.build_test_map`           | RIG-006     | Build/test map for a target.                          |

## Aliasing

The tool loop accepts both the canonical name and a stable alias
only when the alias is explicitly tested. Unknown tool names must
return ``status="error"`` with reason ``unknown_tool``. The free-form
graph-query alias ``cypher`` is intentionally rejected to keep the
graph surface bounded (CCRIG-DD-004).

## Limits

Every tool enforces hard caps:

| Cap                   | Default | Rationale                       |
|-----------------------|---------|---------------------------------|
| max_results           | 100     | per-query-result cap            |
| max_paths_per_result  | 5       | evidence path cap               |
| max_total_chars       | 12 000  | tool envelope cap               |
| max_excerpt_chars     | 2000    | per-evidence cap                |
| max_payload_bytes     | 8 MiB   | RIG snapshot import cap (DD-014) |
| max_entities_per_kind | 50 000  | per-entity cap                   |