# Manual Smoke Checklists (Plugin + TUI)

## Neovim plugin smoke checklist

1. Connect with a valid profile.
2. Run `AnantaAnalyze` on current file.
3. Run `AnantaReview` on a selected code block.
4. Submit one goal with `AnantaGoalSubmit`.
5. Open task context view and artifact preview.
6. Confirm browser handoff shortcut opens expected target.
7. Run `python3 scripts/smoke_nvim_runtime.py` for headless runtime verification.

## TUI smoke checklist

1. Start TUI and verify runtime header context.
2. Open task board and inspect a task detail view.
3. Open artifact list/detail and verify rendering.
4. Open log stream and apply one filter.
5. Open approval queue and inspect one approval detail/action flow.
6. Open audit summary/drill-down and verify trace linkage.
7. Open KRITIS/diagnostics views and verify data visibility.
8. Run `python3 scripts/smoke_tui_runtime.py` for deterministic fixture smoke verification.

## Cross-client golden path smoke

Run:

`python3 scripts/smoke_client_golden_paths.py`

The golden path verifies TUI and Neovim goal->task/artifact flow on fixture transport. Eclipse runtime is currently blocked in this track, so only foundation/manual guidance applies there.
