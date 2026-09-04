import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Route } from '@playwright/test';

import { loginFast } from './utils';

test.describe('DSPy optimization workbench', () => {
  test('supports headless dry-run, lifecycle, promotion, rollback, failure and accessibility', async ({ page, request }) => {
    test.setTimeout(180_000);
    let runState = 'admitted';
    let revision = 1;
    const run = () => ({
      tenant_id: 'tenant-1', run_id: 'run-1', state: runState, revision,
      reason_code: runState === 'cancelled' ? 'dspy_job_cancelled_by_policy' : 'dspy_job_admitted',
      spec_digest: 'b'.repeat(64), artifact: null, usage: null, human_intervention_required: false,
    });
    const dspyRequests: string[] = [];
    const handleDspyRoute = async (route: Route) => {
      if (route.request().method() === 'OPTIONS') {
        await route.fulfill({
          status: 204,
          headers: {
            'access-control-allow-origin': '*',
            'access-control-allow-headers': 'authorization,content-type',
            'access-control-allow-methods': 'GET,POST,OPTIONS',
          },
        });
        return;
      }
      const url = new URL(route.request().url());
      const path = url.pathname;
      dspyRequests.push(`${route.request().method()}:${path}`);
      let data: unknown;
      if (path.endsWith('/capabilities')) {
        data = {
          state: 'available', reason_code: 'dspy_compatible', mode: 'mock', installed_version: '3.2.1',
          compatibility_profile: 'dspy-3.2.1-ananta-v1', optimizer_capabilities: ['labeled_few_shot'],
          program_kinds: ['planning_structured_tasks'], provider_profiles: ['local.default'],
          metric_sets: ['deterministic-v1'], limits: { max_model_calls: 10, max_tokens: 1000 },
          policy_digest: 'a'.repeat(64), human_intervention_required: false,
        };
      } else if (path.endsWith('/runs') && route.request().method() === 'GET') {
        data = { items: [], limit: 100 };
      } else if (path.endsWith('/dry-run')) {
        data = { admissible: true, model_call_performed: false, hard_limits: { max_model_calls: 10 } };
      } else if (path.endsWith('/runs')) {
        data = run();
      } else if (path.endsWith('/cancel')) {
        runState = 'cancelled'; revision += 1; data = run();
      } else if (path.endsWith('/promotion-plans')) {
        data = { state: 'active', reason_code: 'dspy_promoted_by_policy', revision: 1 };
      } else if (path.endsWith('/rollbacks')) {
        data = { state: 'rolled_back', reason_code: 'dspy_rollback_applied', revision: 2 };
      } else if (path.endsWith('/provenance')) {
        data = { schema: 'ananta.dspy-promotion-provenance.v1', current_revision: 2, history: [] };
      } else {
        data = {};
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { 'access-control-allow-origin': '*' },
        body: JSON.stringify({ status: 'success', data }),
      });
      dspyRequests.push(`fulfilled:${route.request().method()}:${path}`);
    };

    await loginFast(page, request);
    await page.evaluate(() => {
      localStorage.setItem('ananta.agents.v1', JSON.stringify([
        { name: 'hub', role: 'hub', url: location.origin, token: '' },
      ]));
    });
    await page.goto('/dspy-optimization');
    await page.route('**/api/dspy-optimization/**', handleDspyRoute);
    await expect(page.getByRole('heading', { name: 'DSPy Optimization' })).toBeVisible();
    await page.getByLabel('Tenant ID').fill('tenant-1');
    await page.getByRole('button', { name: 'Capabilities und Runs laden' }).click();
    await expect.poll(() => dspyRequests).toContain('fulfilled:GET:/api/dspy-optimization/capabilities');
    await expect.poll(() => dspyRequests).toContain('fulfilled:GET:/api/dspy-optimization/runs');
    await expect(page.getByText('local.default')).toBeVisible();
    await page.getByLabel('OptimizationSpec JSON').fill('{"schema":"test"}');
    await page.getByRole('button', { name: 'Dry-run' }).click();
    await expect(page.getByTestId('dspy-dry-run')).toContainText('model_call_performed');
    await page.getByRole('button', { name: 'Policy-konformen Lauf starten' }).click();
    await expect(page.getByRole('heading', { name: 'run-1' })).toBeVisible();
    await page.getByRole('button', { name: 'Automatisch sicher abbrechen' }).click();
    await expect(page.getByRole('status')).toContainText('dspy_job_cancelled_by_policy');
    await page.getByLabel('PromotionPlan JSON').fill('{"schema":"plan"}');
    await page.getByLabel('Attestierte Evaluation JSON').fill('{"attestation":"signed"}');
    await page.getByRole('button', { name: 'Automatische Gates anwenden' }).click();
    await expect(page.getByTestId('dspy-promotion-result')).toContainText('active');
    await page.getByLabel('Scope ID').fill('planning-en');
    await page.getByRole('button', { name: 'Rollback ausführen' }).click();
    await expect(page.getByTestId('dspy-promotion-result')).toContainText('rolled_back');
    await page.getByRole('button', { name: 'Unveränderliche Provenienz laden' }).click();
    await expect(page.getByTestId('dspy-provenance-result')).toContainText('current_revision');
    await page.getByLabel('OptimizationSpec JSON').fill('[]');
    await page.getByRole('button', { name: 'Dry-run' }).click();
    await expect(page.getByRole('status')).toContainText('dspy_document_invalid');
    const axe = await new AxeBuilder({ page }).include('main').analyze();
    expect(axe.violations).toEqual([]);
  });
});
