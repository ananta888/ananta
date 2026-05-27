# Heuristic Bootstrap Catalog

All bootstrap heuristics are **deterministic**, require **no AI calls**, and use
`safety_class: ui_motion_only` (snake) or `safety_class: readonly` (chat/helpcenter/planning).

## Domains

| Domain | Count | Safety class | TTL range |
|---|---|---|---|
| `tui_snake` | 4 | `ui_motion_only` | 5–10 s |
| `snake_eclipse` | 4 | `ui_motion_only` | 5–10 s |
| `chat_codecompass` | 6 | `readonly` | 10–20 s |
| `helpcenter` | 3 | `readonly` | 10–20 s |
| `planning` | 4 | `readonly` | 10–20 s |

## TUI Snake (`tui_snake`)

| Heuristic ID | Strategy | Trigger |
|---|---|---|
| `snake_tui_follow_distance_default` | `TuiFollowDistanceStrategy` | goal present or artifact selected |
| `snake_tui_lurk_focus_default` | `TuiLurkFocusStrategy` | pointer idle / AI offline |
| `snake_tui_artifact_intent_default` | `TuiArtifactIntentStrategy` | artifact selected/highlighted |
| `snake_tui_diff_focus_default` | `TuiDiffFocusStrategy` | diff/compare panel active |

Fallback chain: `artifact_intent → diff_focus → follow_distance → lurk_focus`

## Eclipse Snake (`snake_eclipse`)

| Heuristic ID | Strategy | Zone |
|---|---|---|
| `snake_eclipse_editor_lurk_default` | `EclipseEditorLurkStrategy` | editor / source |
| `snake_eclipse_problem_view_default` | `EclipseProblemViewStrategy` | problems / error_log |
| `snake_eclipse_compare_default` | `EclipseCompareStrategy` | compare / diff / git_compare |
| `snake_eclipse_package_explorer_default` | `EclipsePackageExplorerStrategy` | package_explorer / navigator |

Fallback chain: `package_explorer → problem_view → compare → editor_lurk`

## Chat CodeCompass (`chat_codecompass`)

| Heuristic ID | Strategy | Activates on |
|---|---|---|
| `chat_codecompass_selected_artifact_first` | `SelectedArtifactFirstStrategy` | artifact selected |
| `chat_codecompass_symbol_lookup_default` | `SymbolLookupStrategy` | class/method/type keywords |
| `chat_codecompass_error_lookup_default` | `ErrorLookupStrategy` | error/exception keywords |
| `chat_codecompass_todo_status_default` | `TodoStatusStrategy` | task/todo keywords |
| `chat_codecompass_sourcepack_lookup_default` | `SourcePackLookupStrategy` | API/service/model keywords |
| `chat_codecompass_no_good_match_default` | `NoGoodMatchStrategy` | terminal anti-hallucination guard |

Fallback chain: `selected_artifact_first → symbol_lookup → error_lookup → todo_status → sourcepack_lookup → no_good_match`

## Helpcenter (`helpcenter`)

| Heuristic ID | Strategy | Activates on |
|---|---|---|
| `helpcenter_failure_triage_default` | `FailureTriageStrategy` | fail/broken/regression keywords |
| `helpcenter_github_failure_source_refs_default` | `GithubFailureSourceRefsStrategy` | PR/issue/commit keywords |
| `helpcenter_duplicate_failure_grouping_default` | `DuplicateFailureGroupingStrategy` | duplicate/flaky/known issue keywords |

Fallback chain: `failure_triage → github_failure_refs → duplicate_grouping`

## Planning (`planning`)

| Heuristic ID | Strategy | Activates on |
|---|---|---|
| `planning_next_task_default` | `NextTaskStrategy` | next/prioritize/work on keywords |
| `planning_summary_recompute_default` | `SummaryRecomputeStrategy` | summary/status/recap keywords |
| `planning_archive_done_default` | `ArchiveDoneStrategy` | archive/cleanup/done keywords |
| `planning_related_todo_merge_default` | `RelatedTodoMergeStrategy` | merge/consolidate/related keywords |

Fallback chain: `next_task → summary_recompute → archive_done → related_todo_merge`

## Bootstrap Rules

All bootstrap heuristics enforce:
- `deterministic: true` — no LLM calls
- No `file_write`, `network_access`, or `secret_access` capabilities
- Snake domains: no `write_local_notes`, `send_to_chat`, or `read_source_refs`
- Status `active` in index — candidate promotion requires human approval
- TTL enforced by `HeuristicDecisionLease`; snake leases expire after 5–10 s, chat/helpcenter/planning after 10–20 s

## Files

| Config file | Purpose |
|---|---|
| `heuristics/active/*.heuristic.json` | Individual bootstrap heuristic definitions |
| `heuristics/index.json` | Registry index (v1.4, 26 entries) |
| `heuristics/python_strategy_bindings.json` | Maps heuristic IDs to Python class/module |
| `heuristics/fallback_chains.json` | Per-domain fallback sequence configuration |
| `heuristics/FORMAT_POLICY.md` | Authoring rules, naming conventions, safety classes, OpenCode flow |

## Format System Architecture

### Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  AUTHORING                                                                   │
│   heuristics/authoring/*.heuristic.yaml  ─→  HeuristicYamlImporter         │
│   (operator or AI draft)                      (normalizes to candidate JSON) │
└──────────────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  VALIDATION PIPELINE                                                         │
│   HeuristicFormatValidator   — semantic consistency (version, ttl, mode)    │
│   AiProposalGuardrails       — anti-hallucination (no inline code, snake_  │
│                                 case IDs, no invented caps, no active claim) │
│   HeuristicCatalogValidator  — JSON schema + bootstrap safety rules         │
│   HeuristicProvenanceTracker — content_hash enrichment + verification       │
└──────────────────────────────────────────────────────────────────────────────┘
           │
           ▼  (human approval required — no auto-activation)
┌─────────────────────────────────────────────────────────────────────────────┐
│  ACTIVATION GATE                                                             │
│   HeuristicActivationGate.activate()                                        │
│     Gate 1: schema-valid (ProposalValidator.passed=True)                    │
│     Gate 2: simulation passed (SimulationReport.can_activate=True)          │
│     Gate 3: human_approval_ref in audit log                                 │
│   → writes to heuristics/active/  + emits heuristic_activated audit event  │
│   Rollback: restore archived version                                        │
│   Quarantine: immediately suspend + move to heuristics/quarantine/          │
└──────────────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  RUNTIME                                                                     │
│   HeuristicRegistry loads heuristics/active/*.heuristic.json               │
│   PythonStrategyLoader loads allowlisted Python strategy classes            │
│   HeuristicSelectionService runs fallback chains per domain                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key runtime files

| Module | Role |
|---|---|
| `heuristic_normalizer.py` | Canonical JSON normalization + content_hash |
| `yaml_importer.py` | YAML→candidate JSON import |
| `format_validator.py` | Semantic consistency checks |
| `ai_proposal_guardrails.py` | Anti-hallucination guardrails for AI proposals |
| `provenance_tracker.py` | Provenance enrichment and hash verification |
| `heuristic_catalog_validator.py` | Schema + bootstrap safety validation |
| `activation_gate.py` | Activation/rollback/quarantine with audit trail |
| `python_strategy_loader.py` | Allowlisted Python strategy class loader |
| `heuristic_format_tui_view.py` | TUI status view for format system |
| `agent/cli/commands/heuristic.py` | CLI: list, show, validate, normalize, catalog |

### Operator CLI quick-reference

```bash
# List all active heuristics
ananta heuristic list

# Inspect one heuristic
ananta heuristic show chat_codecompass_symbol_lookup_default

# Validate a file
ananta heuristic validate heuristics/active/my.heuristic.json

# Normalize a YAML draft (dry-run)
ananta heuristic normalize heuristics/authoring/draft.heuristic.yaml --dry-run

# Validate the whole catalog
ananta heuristic catalog
```
