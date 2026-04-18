# AGENTS.md

This is a scoped OpenCode workspace for the Ananta project.

## Mandatory architecture rules
- The hub remains the control plane and owns orchestration, routing, policy, and the task queue.
- Workers execute delegated work only.
- Do not introduce worker-to-worker orchestration.
- Preserve container boundaries and avoid implicit shared state.
- Prefer additive, backward-compatible changes over breaking redesigns.

## Engineering rules
- Keep changes small, testable, and SOLID.
- Reuse existing abstractions before adding new ones.
- Keep behavior observable; do not hide failures.
- Respect the task workspace as the primary place for new files and generated context.
- The workspace may be reused across related delegated tasks; keep state intentional and auditable.

## Workspace guidance
- Read `.ananta/context-index.md` first for task-specific context files.
- Use `rag_helper/` for retrieved research and knowledge files when present.
- Follow `.ananta/response-contract.md` for the required response format.
