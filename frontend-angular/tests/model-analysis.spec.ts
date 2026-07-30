import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { loginFast } from './utils';

test.describe('Model analysis bounded workflow', () => {
  test('runs an import reference to report with no critical accessibility violations', async ({ page, request }) => {
    await loginFast(page, request);

    await page.route('**/api/model-intelligence/**', async route => {
      const url = new URL(route.request().url());
      const method = route.request().method();
      const path = url.pathname;
      if (path.endsWith('/capabilities')) {
        return route.fulfill({ json: {
          supported: true,
          max_graph_nodes: 200,
          max_graph_edges: 400,
        } });
      }
      if (path.endsWith('/jobs') && method === 'GET') {
        return route.fulfill({ json: { items: [], next_cursor: null } });
      }
      if (path.endsWith('/jobs') && method === 'POST') {
        return route.fulfill({ json: fixtureJob });
      }
      if (path.endsWith('/jobs/job-fixture')) {
        return route.fulfill({ json: fixtureJob });
      }
      if (path.endsWith('/jobs/job-fixture/report')) {
        return route.fulfill({ json: {
          schema: 'ananta.model-intelligence-report.v1',
          content_digest: `sha256:${'a'.repeat(64)}`,
          sections: [
            { name: 'static', status: 'available', data: { parameter_count: 7 } },
            { name: 'trace', status: 'unsupported', reason_code: 'runtime_no_trace' },
          ],
        } });
      }
      if (path.endsWith('/jobs/job-fixture/graph')) {
        return route.fulfill({ json: {
          schema: 'model_graph.v1',
          nodes: [{ node_id: 'model-1', label: 'Fixture Model', kind: 'model' }],
          edges: [],
          truncated: false,
        } });
      }
      return route.fulfill({ status: 404, json: { reason_code: 'fixture_route_missing' } });
    });

    await page.goto('/model-analysis');
    await expect(page.getByRole('heading', { name: 'Open-Weight Modellanalyse' })).toBeVisible();
    await expect(page.getByText('Noch keine Analysejobs')).toBeVisible();
    await page.getByLabel('Importreferenz').fill('import:model-fixture');
    await page.getByRole('button', { name: 'Analyse delegieren' }).click();

    await expect(page.getByRole('heading', { name: 'Kanonischer Report' })).toBeVisible();
    await expect(page.getByText('runtime_no_trace')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Modellgraph' })).toBeVisible();
    await expect(page.getByText('Fixture Model')).toBeVisible();

    const accessibility = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    expect(accessibility.violations).toEqual([]);
  });
});

const fixtureJob = {
  schema: 'ananta.model-intelligence.analysis-job.v1',
  job_id: 'job-fixture',
  hub_task_id: 'hub-task-fixture',
  model_id: 'model-fixture',
  analysis_kind: 'full',
  profile_id: 'bounded-ui',
  request_sha256: 'b'.repeat(64),
  requested_artifact_kinds: ['report', 'model_graph'],
  max_runtime_seconds: 300,
  max_output_bytes: 1048576,
  status: 'completed',
  progress_percent: 100,
};
