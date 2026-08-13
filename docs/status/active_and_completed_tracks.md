# Active and Completed Todo Tracks

This document is a compact inventory to avoid duplicate track creation. Detailed scope stays in each active track file. Completed or removed tracks are listed separately as historical references.

## Active OSS tracks (working set)

| File | Track | Scope role |
| --- | --- | --- |
| `todos/todo.caseflow-agent-collaboration-canvas.json` | `caseflow-agent-collaboration-canvas` | Active backlog for the CaseFlow agent collaboration canvas, reusing the existing workflow, agent, and trace stacks |
| `todos/todo.decentralized-webrtc-peer-media-overlay.json` | `decentralized-webrtc-peer-media-overlay` | Active backlog for decentralized WebRTC group topology and the experimental encrypted peer-media overlay |
| `todos/todo.duckdb-codecompass-analytics-retrieval-integration.json` | `duckdb_codecompass_analytics_retrieval_integration` | Active backlog for the optional DuckDB analytics and retrieval backend for Ananta and CodeCompass |
| `todos/todo.knowledge-hygiene-curated-markdown-wiki-conflict-resolution.json` | `knowledge_hygiene_curated_markdown_wiki_conflict_resolution` | Active backlog for curated knowledge hygiene, conflict review, and human decisions |
| `todos/todo.langextract-codecompass-claim-extraction-adapter.json` | `langextract_codecompass_claim_extraction_adapter` | Active backlog for the optional provider-neutral LangExtract claim-extraction strategy |
| `todos/todo.local-moe-root-cause-routing-codecompass-behavioral-evaluation.json` | `local-moe-root-cause-routing-codecompass-behavioral-evaluation` | Active backlog for local MoE fit, root-cause routing, and behavioral evaluation |
| `todos/todo.pair-dev-collaboration-workspace-buzz-interop.json` | `pair-dev-collaboration-workspace-buzz-interop` | Active backlog for the native Pair-Dev collaboration workspace and optional Buzz interoperability |

## Deferred KRITIS / Enterprise-related scope

| File | Track | Scope state |
| --- | --- | --- |
| `todo.kritis.json` | `kritis_hardening_program` | Deferred for OSS release focus; used as explicit gate references |

## Completed / archived references

| File | State | Evidence pointer |
| --- | --- | --- |
| `todo.doc.json` | Completed and removed | Documentation reconciliation completed before removal; evidence remains in `docs/status/documentation-command-contract.json`, `docs/status/documentation-command-usage.md`, `docs/status/documentation-drift-decision-matrix.md`, `docs/status/architecture-source-map.md`, and `docs/status/architecture-drift-report.md` |
| `todo_last.json` | Completed historical track snapshot | `todo_last.json` shows all tasks in `done` state |

## Removed / inactive legacy references

| File reference | State | Evidence pointer |
| --- | --- | --- |
| `todo.eclipse.json` | Not present / inactive | Former Eclipse productization working-set reference; do not restore the root file unless a new active backlog is intentionally opened |
| `todo.wiki-rag2.json` | Not present / inactive | Former Wiki RAG v2 productionization working-set reference; do not restore the root file unless a new active backlog is intentionally opened |
| `todo.security.json` | Not present / inactive | `todo.json` analysis notes document stale reference cleanup |
| `todo.ananta-worker.json` | Not present / inactive | `todo.json` analysis notes document stale reference cleanup |

## Usage rule

Use this inventory only for orientation. Planning and execution decisions must still come from the detailed task definitions inside each active track file.
