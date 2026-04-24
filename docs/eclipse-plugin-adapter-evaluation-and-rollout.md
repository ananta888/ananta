# Eclipse Plugin Adapter Evaluation and Rollout

## OpenAI-compatible fallback evaluation (ECL-T16)

- Result: technically possible as optional compatibility path.
- Decision: keep only as bounded fallback.
- Rationale: main path remains native Ananta goal/task flows to preserve governance and orchestration consistency.

## MCP integration path evaluation (ECL-T17)

- Result: feasible but comparatively heavier for Eclipse plugin scope.
- Decision: prefer direct REST integration for the mainline plugin.
- Rationale: REST keeps adapter complexity lower and maintenance simpler.

## Golden path demo (ECL-T23)

1. Select code in Eclipse editor.
2. Submit a goal via goal input panel.
3. Inspect returned task in task view.
4. Inspect related artifact in artifact view.
5. Open browser deep-link for advanced details if needed.

## Manual smoke checklist (ECL-T25)

1. Connect using configured profile.
2. Send selection/context payload.
3. Run analyze flow.
4. Run review flow.
5. Confirm task/artifact/shortcut behavior.

## Future roadmap (ECL-T26)

- Inline suggestions (later phase)
- Richer diff/review rendering (later phase)
- Deeper task board interactions (later phase)

These are explicitly out of MVP scope to keep the adapter maintainable and thin.

## Runtime delivery status for current CRT track

- Eclipse runtime command/view MVP is active for the EAC track.
- Runtime artifacts include plugin metadata, deterministic dockerized build command, command registry, bounded context capture, runtime views extension registry and runtime smoke checks.
- Hardening block is implemented: deeper runtime Java test coverage, headless smoke gate, CI lane and merge-readiness evidence.
