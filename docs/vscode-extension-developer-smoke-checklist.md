# VS Code Extension Developer Smoke Checklist

## Automated checks

Run from `client_surfaces/vscode_extension`:

1. `npm ci`
2. `npm run lint`
3. `npm run test`
4. `npm run test:smoke`
5. `npm run compile`
6. `npm run package`

Expected success signals:

- Lint exits clean.
- Vitest suite passes (`*.test.ts` and `extension.smoke.test.ts`).
- Compile produces `out/` without TypeScript errors.
- Packaging creates `dist/ananta-vscode-extension.vsix`.

Expected failure signals:

- Capability or contract regressions fail test assertions.
- Missing runtime evidence breaks smoke/bootstrap tests.
- Type/lint regressions block compile/package.

## Manual checks (release review)

1. Install or run extension in VS Code.
2. Configure backend profile (`baseUrl`, `profileId`, auth mode/token).
3. Run `Ananta: Check Backend Health` and confirm status changes to connected/degraded correctly.
4. In editor, run `Ananta: Analyze Selection`.
5. Run `Ananta: Review File`.
6. Run `Ananta: Submit Goal` and confirm task/result handling.
7. Open one task detail and one artifact detail from sidebar.
8. Open browser fallback from Runtime view and confirm target URL.
9. Launch `Ananta: Launch TUI in Terminal`.

Expected success signals:

- Commands are visible and capability-gated.
- Sidebar views load data or explicit degraded states.
- Context preview appears before workflow submission.
- No implicit file mutation occurs during workflow commands.

Expected failure/degraded signals:

- Unauthorized/policy-denied responses show warnings and disabled actions.
- Missing capabilities prevent command execution with explicit feedback.
- Unsupported/binary artifact types switch to browser fallback.
