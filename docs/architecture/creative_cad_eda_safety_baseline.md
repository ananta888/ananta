# Creative CAD EDA Safety Baseline

## Safety classes

1. **read_only**: inspection, listing, parsing metadata.
2. **planning**: generate proposals/scripts/macros without execution.
3. **preview_or_export_plan**: non-mutating preview/export intent.
4. **mutation**: state-changing model/scene/pcb operations.
5. **script_or_macro_execution**: execution of generated or supplied code/macros.

## Cross-domain strictness

| Class | Blender baseline | FreeCAD baseline | KiCad baseline |
| --- | --- | --- | --- |
| read_only | allow | allow | allow |
| planning | allow | allow | allow |
| preview_or_export_plan | allow | allow | allow |
| mutation | approval_required | default_deny + approval_required | default_deny + approval_required |
| script_or_macro_execution | approval_required | default_deny + approval_required | default_deny + approval_required |

## Mandatory governance rules

- Any file write, scene/model/pcb mutation, script execution, or macro execution requires explicit approval.
- Every approved mutation/execution path must emit auditable execution evidence.
- Default policy behavior for mutating and executable actions is deny-oriented unless explicitly opened.
- No domain may bypass hub policy, approval or audit flows.

## Safety rationale

This baseline preserves creative iteration for Blender while enforcing stricter correctness boundaries for CAD/EDA domains.
