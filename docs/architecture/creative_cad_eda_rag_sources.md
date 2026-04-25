# Creative CAD EDA RAG Source Conventions

## Source categories

1. domain docs and architecture specs
2. API docs and command references
3. source code and adapters
4. examples and sample projects
5. plugins/addons integration snippets
6. verification rules and known constraints

## Ingestion rule

All ingestion and retrieval must flow through `codecompass` / `rag_helper` conventions. No domain-specific bypass ingestion paths are allowed.

## Profile expectations by domain

- **Blender**: addon docs, scripting docs, bridge adapter notes, preview/export patterns.
- **FreeCAD**: document/model APIs, macro guidance, export constraints, correctness checks.
- **KiCad**: project/board APIs, ERC/DRC references, BOM/export constraints, manufacturing rule docs.

## Verification guidance

- Prefer bounded domain-scoped queries.
- Preserve citations for all retrieved chunks.
- Treat generated scripts/macros as untrusted outputs until policy + approval gates pass.
