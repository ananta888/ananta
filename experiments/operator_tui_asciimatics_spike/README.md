# asciimatics Spike — Optional Terminal Effects Backend

## Setup

```bash
pip install asciimatics
```

## Evaluation

| Aspect | Assessment |
|--------|-----------|
| Animation model | `Animation` + `Scene` + `Effect` classes; screen buffer with per-character color/depth |
| Terminal fit | Designed for terminal animations (fireworks, progress, text scroll) — not geometry/3D projection |
| Dependency weight | ~1 MB, pure Python, no native deps |
| Integration with prompt_toolkit | asciimatics owns the full screen (`Screen.open`) — conflicts with prompt_toolkit's `Application`. Both fight for stdin/stdout. |
| Splash-only scenario | Could run asciimatics screen for 2s splash then exit back to prompt_toolkit, but screen ownership handoff is fragile (terminal state reset, alternate screen buffer) |
| 3D geometry support | None built-in — same projection math would need to be implemented, just rendered via asciimatics `Colour`/`Canvas` primitives instead of raw ANSI strings |

## Comparison vs. BuiltinBackend

| Capability | BuiltinBackend | asciimatics path |
|-----------|----------------|-----------------|
| 3D projection | Same custom math needed | Same custom math needed |
| ANSI color | Direct ANSI SGR | via asciimatics Colour enum (256-color subset) |
| Frame loop | prompt_toolkit `invalidate()` | asciimatics `Scene`/`Effect` loop |
| Testability | Pure string output, no screen | Requires mocked Screen object |
| Dependencies | Zero | asciimatics + transitive deps |

## Recommendation

**Reject.** asciimatics provides no advantage over the existing approach. The same projection/shading code would be needed, and the screen ownership conflict with prompt_toolkit adds fragility without benefit. Keep in mind for future standalone terminal tools (non-TUI), not for the Operator TUI.
