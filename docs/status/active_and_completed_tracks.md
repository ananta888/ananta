# Active and Completed Todo Tracks

This document is a compact inventory to avoid duplicate track creation. Detailed scope stays in each active track file. Completed or removed tracks are listed separately as historical references.

## Active OSS tracks (working set)

| File | Track | Scope role |
| --- | --- | --- |
| `todo.json` | `core_boundary_plugin_architecture` | Core-boundary/provider-plugin architecture and integration hygiene |
| `todo.blender.json` | `blender_control_surface_and_runtime` | Blender integration backlog |
| `todo.freecad.json` | `freecad_integration` | FreeCAD integration backlog |
| `todo.kicad.json` | `kicad_integration` | KiCad integration backlog |
| `todo.n8n-integration.json` | `workflow_automation_adapter_layer` | Workflow automation integration backlog |

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
| `todo.security.json` | Not present / inactive | `todo.json` analysis notes document stale reference cleanup |
| `todo.ananta-worker.json` | Not present / inactive | `todo.json` analysis notes document stale reference cleanup |

## Usage rule

Use this inventory only for orientation. Planning and execution decisions must still come from the detailed task definitions inside each active track file.
