# Active and Completed Todo Tracks

This document is a compact inventory to avoid duplicate track creation. Detailed scope stays in each active track file. Completed or removed tracks are listed separately as historical references.

## Active OSS tracks (working set)

| File | Track | Scope role |
| --- | --- | --- |
| `todos/active/todo.agent-defense-in-depth-adversarial-escape-safety.json` | `agent_defense_in_depth_adversarial_escape_safety` | Defense-in-depth implementation is largely complete; grounded external release evidence remains fail-closed |
| `todos/active/todo.ananta-local-multi-model-runtime-and-automated-needle-training.json` | `ananta-local-multi-model-runtime-and-automated-needle-training` | Repository implementation is complete; real dataset, lifecycle, and registered release evidence remain fail-closed |
| `todos/active/todo.codecompass-dmoe-parametric-knowledge-injection.json` | `codecompass_dmoe_parametric_knowledge_injection` | Repository-side DMoE implementation is complete; dynamic-runtime and real-model evidence remain unverified |
| `todos/active/todo.codecompass-sira-corpus-discriminative-retrieval.json` | `codecompass_sira_corpus_discriminative_retrieval` | SIRA implementation is nearly complete and awaits authoritative source and run evidence |
| `todos/active/todo.free-coding-agent-cli-provider-integrations.json` | `free-coding-agent-cli-provider-integrations` | Headless coding-agent integrations are implemented; remaining external runtime evidence stays active |
| `todos/active/todo.mlintern-multi-training-backends-axolotl-llamafactory-autotrain-torchtune.json` | `mlintern_multi_training_backends` | Optional training backends are code-complete or partial and await real GPU and release evidence |
| `todos/todo.decentralized-webrtc-peer-media-overlay.json` | `decentralized-webrtc-peer-media-overlay` | Active backlog for decentralized WebRTC group topology and the experimental encrypted peer-media overlay |
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
| `todos/archiv/todo.agent-cognitive-style-role-profiles.json` | Completed and archived | Cognitive-style contracts, routing integration, drift handling, and source-bound release gates are complete |
| `todos/archiv/todo.caseflow-agent-collaboration-canvas.json` | Completed and archived | All 17 tasks and five milestones are complete; the track reuses the existing workflow, agent, and trace stacks |
| `todos/archiv/todo.central-model-selection-settings.json` | Completed and archived | All 24 tasks and six milestones for canonical model selection are complete |
| `todos/archiv/todo.duckdb-codecompass-analytics-retrieval-integration.json` | Completed and archived | Optional DuckDB analytics and retrieval backend implementation is archived with its final task evidence |
| `todos/archiv/todo.knowledge-hygiene-curated-markdown-wiki-conflict-resolution.json` | Completed and archived | Curated knowledge hygiene, conflict review, and human-decision workflow is archived with its final task evidence |
| `todos/archiv/todo.langextract-codecompass-claim-extraction-adapter.json` | Completed and archived | Provider-neutral LangExtract claim-extraction strategy is archived with its final task evidence |
| `todos/archiv/todo.local-moe-root-cause-routing-codecompass-behavioral-evaluation.json` | Completed and archived | Local MoE fit, root-cause routing, and behavioral evaluation is archived with its final task evidence |
| `todos/archiv/todo.scrum-continuous-improvement-retrospective-loop.json` | Completed and archived | All 28 retrospective-loop tasks are complete with automated, non-HITL verification |

## Removed / inactive legacy references

| File reference | State | Evidence pointer |
| --- | --- | --- |
| `todo.eclipse.json` | Not present / inactive | Former Eclipse productization working-set reference; do not restore the root file unless a new active backlog is intentionally opened |
| `todo.wiki-rag2.json` | Not present / inactive | Former Wiki RAG v2 productionization working-set reference; do not restore the root file unless a new active backlog is intentionally opened |
| `todo.security.json` | Not present / inactive | `todo.json` analysis notes document stale reference cleanup |
| `todo.ananta-worker.json` | Not present / inactive | `todo.json` analysis notes document stale reference cleanup |

## Usage rule

Use this inventory only for orientation. Planning and execution decisions must still come from the detailed task definitions inside each active track file.
