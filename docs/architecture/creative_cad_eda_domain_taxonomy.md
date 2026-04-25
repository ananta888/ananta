# Creative CAD EDA Domain Taxonomy

## Purpose

This taxonomy separates Blender, FreeCAD and KiCad responsibilities while preserving one shared hub-governed control plane.

## Domain classes

1. **Creative scene domains** (example: Blender): iteration-heavy, preview-first, reversible exploration.
2. **Mechanical CAD domains** (example: FreeCAD): geometry correctness, dimensional integrity, stricter mutation governance.
3. **EDA/PCB domains** (example: KiCad): electrical and manufacturability correctness, highest mutation/export safeguards.

## Why Blender differs from FreeCAD/KiCad

- Blender workflows frequently tolerate preview-centric experimentation.
- FreeCAD and KiCad workflows require stronger correctness gates before mutation or production export.
- Therefore mutation defaults are stricter for FreeCAD/KiCad even when naming conventions are shared.

## Shared terms

- **context**: bounded input facts for planning or verification.
- **finding**: non-mutating observation with traceable source.
- **plan**: proposed action sequence without execution.
- **preview**: safe representation of expected result, non-committal.
- **export**: artifact generation or delivery intent.
- **mutation**: operation that changes domain state or files.
- **execution report**: audited outcome of an executed operation.
- **verification artifact**: evidence proving correctness or readiness checks.

## Architectural constraint

All three classes remain thin domain surfaces. Hub orchestration, approval and policy decisions stay centralized.
