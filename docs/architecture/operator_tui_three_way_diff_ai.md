# Architektur: Operator TUI Drei-Wege-Diff + KI-Panel

## 1) Panel-Quellenauswahl

```mermaid
flowchart TD
    U[Operator :diff3 command] --> C[commands.py]
    C --> S[diff3_state]
    S --> P1[Panel A source]
    S --> P2[Panel B source]
    S --> P3[Panel C source]
```

## 2) Resolver -> Engine -> Renderer

```mermaid
flowchart LR
    SRC[DiffSourceRef] --> R[DiffSourceResolver]
    R --> E[DiffEngine]
    E --> PAYLOAD[diff3 payload panel_summaries]
    PAYLOAD --> RENDER[renderer.py diff3 view]
```

## 3) KI-Panel Pipeline

```mermaid
flowchart LR
    AI[:diff3 ai run] --> CE[ContextEnvelope]
    CE --> PROMPT[Prompt Template + Final Prompt]
    PROMPT --> DISPATCH[AI Dispatch]
    DISPATCH --> RESP[ai_diff_response.v1 validation]
    RESP --> STATE[AI panel state completed/degraded]
```

## 4) GoalArtifact + ExecutionProvenance Integration

```mermaid
flowchart TD
    CE[ContextEnvelope artifact refs] --> USAGE[SourceArtifactUsage]
    DISPATCH[AI dispatch response] --> OUT[GoalOutputArtifact]
    DISPATCH --> PROV[ExecutionProvenance]
    USAGE --> OUT
    PROV --> OUT
```

## Testmatrix

| Bereich | Abdeckung |
|---|---|
| State/Sources/Engine | `test_three_way_diff_state_schema.py`, `test_tui_diff_sources.py`, `test_tui_diff_source_resolver.py`, `test_tui_diff_engine.py` |
| Commands/Renderer | `test_tui_diff3_commands.py`, `test_tui_diff3_renderer.py` |
| AI Mock/Dispatch | `test_tui_ai_diff_panel_state.py`, `test_tui_ai_diff_context_dispatch.py` |
| E2E | `scripts/e2e/diff3_two_current_plus_ai_e2e.py`, `scripts/e2e/diff3_flexible_sources_e2e.py` |

