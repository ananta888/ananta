# Creative CAD EDA Artifact Families

## Family set

- findings
- action_plans
- script_or_macro_plans
- preview_renders
- export_plans
- verification_reports
- execution_reports

## Mapping intent

These families are shared abstractions that later map into Blender-, FreeCAD- and KiCad-specific artifact schemas.

## Safety and verification expectations

| Family | Safety class | Default verification |
| --- | --- | --- |
| findings | read_only | basic |
| action_plans | planning | enhanced |
| script_or_macro_plans | planning | enhanced |
| preview_renders | preview | enhanced |
| export_plans | planning | strict |
| verification_reports | read_only | strict |
| execution_reports | execution | strict |

## Domain emphasis

- Blender emphasizes preview_renders and action_plans.
- FreeCAD emphasizes verification_reports for geometry and tolerance checks.
- KiCad emphasizes verification_reports for ERC/DRC and manufacturing-readiness checks.
