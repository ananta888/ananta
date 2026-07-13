import { expect, test } from '@playwright/test';

import { HUB_URL, loginFast } from './utils';

test.describe('Workflow runtime operations', () => {
  test('shows loading then degraded and stale runtime evidence from the Hub read model', async ({ page, request }) => {
    await loginFast(page, request);
    const requests: string[] = [];
    let releaseResponse!: () => void;
    const responseGate = new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });
    await page.route('**/api/workflow-runtime/operations?*', async (route) => {
      requests.push(route.request().url());
      await responseGate;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(runtimeResponse([degradedRun()])),
      });
    });

    await page.goto('/workflow-runtime-operations', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('runtime-operations-loading')).toBeVisible();
    releaseResponse();
    await expect(page.getByRole('heading', { name: 'Workflow-Runtime Operations' })).toBeVisible();
    await expect(page.getByText('Unbestätigt · Evidence fehlt')).toBeVisible();
    await expect(page.getByTestId('runtime-stale-warning')).toBeVisible();
    await expect(page.getByText('native_interrupt_gap')).toBeVisible();
    await expect(page.getByText('langgraph → native')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Recovery starten' })).toBeDisabled();

    expect(requests.length).toBeGreaterThan(0);
    for (const url of requests) {
      expect(new URL(url).origin).toBe(new URL(HUB_URL).origin);
    }
  });

  test('distinguishes empty and forbidden Hub responses', async ({ page, request }) => {
    await loginFast(page, request);
    let forbidden = false;
    await page.route('**/api/workflow-runtime/operations?*', async (route) => {
      if (forbidden) {
        await route.fulfill({
          status: 403,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'error', reason_code: 'runtime_operations_forbidden' }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(runtimeResponse([])),
      });
    });

    await page.goto('/workflow-runtime-operations', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('runtime-operations-empty')).toBeVisible();

    forbidden = true;
    await page.getByRole('button', { name: 'Aktualisieren' }).click();
    await expect(page.getByTestId('runtime-operations-forbidden')).toBeVisible();
    await expect(page.getByText('Zugriff verweigert')).toBeVisible();
  });
});

function runtimeResponse(runs: any[]) {
  return {
    schema: 'ananta.workflow_runtime_operations_list.v1',
    generated_at: Date.now() / 1000,
    filters: {},
    summary: {
      total_runs: runs.length,
      degraded_runs: runs.filter((run) => run.degraded).length,
      stale_runs: runs.filter((run) => run.stale).length,
      unverified_successes: runs.filter((run) => run.outcome_claim === 'unverified').length,
      open_gates: runs.reduce((sum, run) => sum + run.open_gate_count, 0),
      verified_evidence: runs.reduce((sum, run) => sum + run.verified_evidence_count, 0),
      total_cost_micros: runs.reduce((sum, run) => sum + run.cost_micros, 0),
      latency_p50_ms: runs[0]?.latency_ms || 0,
      latency_p95_ms: runs[0]?.latency_ms || 0,
      active_recoveries: 0,
      parity_gap_runs: runs.filter((run) => run.parity_gaps.length > 0).length,
    },
    runs,
    count: runs.length,
  };
}

function degradedRun() {
  return {
    schema: 'ananta.workflow_runtime_operations_record.v1',
    run_id: 'run-e2e',
    workflow_id: 'workflow-e2e',
    task_id: 'task-e2e',
    runtime: 'langgraph',
    mode: 'compiled',
    status: 'completed',
    outcome_claim: 'unverified',
    capabilities: [{ name: 'checkpoint', status: 'supported', reason_code: null }],
    fallbacks: [{
      source_runtime: 'langgraph', target_runtime: 'native', reason_code: 'compiled_failed',
      semantic_class: 'control_flow_changed', approved: true,
    }],
    cost_micros: 1200,
    latency_ms: 73.4,
    recovery: { status: 'degraded', strategy: 'checkpoint', attempts: 2, last_checkpoint_ref: 'cp-e2e', reason_code: 'resume_pending' },
    gates: [{
      gate_id: 'gate-e2e', label: 'Operator Gate', status: 'open', approval_id: null,
      required_evidence_refs: [], allowed_commands: [], expires_at: null,
    }],
    evidence: [{
      evidence_id: 'ev-e2e', kind: 'acceptance', verification_status: 'unverified',
      summary: 'Artifact pending', source_ref: null, observed_at: Date.now() / 1000,
    }],
    parity_gaps: [{ code: 'native_interrupt_gap', category: 'parity', severity: 'warning', summary: 'Interrupt differs' }],
    semantic_deviations: [],
    open_gate_count: 1,
    verified_evidence_count: 0,
    degraded: true,
    degraded_reasons: ['fallback_observed', 'native_parity_gap', 'success_without_verified_evidence'],
    stale: true,
    updated_at: Date.now() / 1000 - 120,
    stale_after_seconds: 30,
    source_sequence: 8,
  };
}
