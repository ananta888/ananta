# Domain Capability Naming

## Canonical format

Capabilities use:

`<domain>.<resource>.<action>`

Examples:

- `blender.scene.read`
- `freecad.document.read`
- `kicad.drc.plan`

## Action semantics

- `read` / `inspect`: non-mutating lookup.
- `plan`: proposal generation without execution.
- `mutate`: explicit state change.
- `execute`: code/macro/router/export execution.

Naming must separate:

- plan vs execute
- read/inspect vs mutate

## Conventions

- Keep domain prefix stable (`blender`, `freecad`, `kicad`).
- Use resource nouns (`scene`, `document`, `drc`, `export`, `macro`, `routing`).
- Use action verbs from the allowed semantic set.
- Avoid overloaded capability IDs that hide mutating behavior.

## Seed examples

- Blender: `blender.preview.plan`, `blender.script.execute`
- FreeCAD: `freecad.export.plan`, `freecad.model.mutate`, `freecad.macro.execute`
- KiCad: `kicad.erc.plan`, `kicad.pcb.mutate`, `kicad.export.execute`
