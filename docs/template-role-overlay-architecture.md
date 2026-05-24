# Template, Role, Overlay, and Evolver Architecture

Status: Working architecture baseline.

## Scope Separation

- Planning templates: deterministic subtask generation during planning.
- Role/team/blueprint templates: execution-style context for a role, not subtask generation.
- User profile and task overlay: bounded preference layers (style/language/detail/working mode/formatting).
- Governance/security/approval/tool/runtime policy: dominant and non-overridable by profile/overlay.
- Planning prompt evolver: planning-only optimization, not worker prompt mutation.

## Goal to Worker Flow

```mermaid
flowchart TD
    A[Goal] --> B[Planning]
    B --> C[PlanNode]
    C --> D[Task]
    D --> E[Role Template Resolution]
    D --> F[Instruction Layer Selection]
    E --> G[InstructionStackArtifact]
    F --> G
    G --> H[ProposeContext]
    H --> I[Strategy Prompt Composition]
    I --> J[Worker/LLM]
```

## Planning Template vs Role Template

```mermaid
flowchart TD
    A[Goal / mode_data.template_id] --> B[PlanningTemplateCatalog]
    B --> C[Subtasks]
    D[Role/Team/Blueprint template resolution] --> E[Role template context]
    E --> F[Instruction stack layer: blueprint_template]
```

## Instruction Layer Order

```text
governance > blueprint_template > user_profile > task_overlay > task_input
```

## Prompt Evolver Boundary

```mermaid
flowchart TD
    A[PlanningRun telemetry] --> B[PlanningPromptEvolverService]
    B --> C[PlanningPromptVersionDB]
    C --> D[Future planning runs]
    B -. forbidden .-> E[Worker system prompt]
    B -. forbidden .-> F[Role templates]
    B -. forbidden .-> G[Profiles/overlays]
    B -. forbidden .-> H[Governance/tool contracts]
```

Allowed evolver impact:
- planning prompt text/hints
- planning output-format guidance for future planning runs

Forbidden evolver impact:
- worker prompt composition
- role template content
- overlay/profile permissions
- governance/security/approval/tool/runtime policy
