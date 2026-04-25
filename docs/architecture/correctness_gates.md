# Correctness Gates for Creative CAD EDA

## Gate levels

1. `creative_preview`
2. `reversible_scene_change`
3. `mechanical_model_change`
4. `pcb_design_change`
5. `production_export`

## Domain defaults

- Blender defaults around `creative_preview` and escalates for mutation.
- FreeCAD defaults to stricter levels for model mutation and export.
- KiCad defaults to strictest levels for PCB changes and manufacturing export.

## Policy and approval integration

- Gate levels are referenced by policy metadata and approval context.
- Higher gates require stronger verification evidence and explicit operator approval.
- Any production export gate requires auditable verification artifacts.

## Practical rule

If an action may alter authoritative CAD/EDA state or manufacturing outputs, it must not run under preview-level gates.
