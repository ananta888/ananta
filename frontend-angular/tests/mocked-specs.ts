/**
 * Playwright specs that run entirely against mocked routes: no Hub/worker backend is needed.
 * `playwright.mocked.config.ts` runs exactly these, in parallel and without starting the backend.
 * A spec belongs here only when it passes in that configuration and writes no committed evidence
 * (visual-process-assistant-performance.spec.ts records gate evidence and runs via its own config).
 */
export const MOCKED_SPECS: string[] = [
  'caseflow-agent-collaboration.spec.ts',
  'central-model-settings.spec.ts',
  'kanban-cross-surface-live-hub.spec.ts',
  'source-control-vertical-test-support.spec.ts',
  'visual-process-assistant-isolation.spec.ts',
  'visual-process-assistant-patch.spec.ts',
];
