# Textual Spike — Optional 3D Animation Backend

## Setup

```bash
pip install textual
```

## Evaluation

| Aspect | Assessment |
|--------|-----------|
| Animation model | Textual has `set_interval`/`set_timer` for periodic widget updates; no built-in 3D projection primitives |
| Terminal fit | Full TUI framework with layout, CSS widgets — replaces prompt_toolkit entirely, not a drop-in backend |
| Dependency weight | ~5 MB install + transitive deps, locks into Textual API surface |
| Migration cost | Complete rewrite of `InteractiveOperatorTui` and all rendering — not scoped for MVP |
| Integration with splash | Could host the 3D animation, but the existing `SplashMachine` lifecycle would need to be ported to Textual's `App`/`Screen` model |

## Feasibility for splash-only use

Theoretically possible: run a Textual `App` for the splash duration, render the 3D frame as `Static` widget content, then exit back to prompt_toolkit. In practice this means two TUI frameworks in the same process — fragile startup, double event loop complexity.

## Recommendation

**Reject for MVP. Keep as a future consideration** if the entire Operator TUI is migrated to Textual. Do NOT pull Textual in for splash-only usage. The custom `BuiltinBackend` + prompt_toolkit `TextArea.text` invalidation is simpler and dependency-free.
