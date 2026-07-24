import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect, Page, test } from '@playwright/test';

type JsonRecord = Record<string, unknown>;

const hubUrl = process.env['CROSS_SURFACE_HUB_URL'] || '';
const token = process.env['CROSS_SURFACE_TOKEN'] || '';
const python = process.env['CROSS_SURFACE_PYTHON'] || '';
const repositoryRoot = process.env['CROSS_SURFACE_REPOSITORY_ROOT'] || '';
const liveHarnessConfigured = Boolean(hubUrl && token && python && repositoryRoot);

test.skip(
  !liveHarnessConfigured,
  'requires the pytest-owned live Hub cross-surface harness',
);

function record(value: unknown, label: string): JsonRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return value as JsonRecord;
}

function runTuiProbe(...arguments_: string[]): JsonRecord {
  const output = execFileSync(
    python,
    [
      join(
        repositoryRoot,
        'tests',
        'client_surfaces',
        'operator_tui',
        'kanban_cross_surface_probe.py',
      ),
      '--endpoint',
      hubUrl,
      '--token',
      token,
      ...arguments_,
    ],
    {
      cwd: repositoryRoot,
      encoding: 'utf8',
      env: process.env,
      timeout: 20_000,
    },
  );
  return record(JSON.parse(output) as unknown, 'TUI probe response');
}

function angularCard(page: Page, title: string) {
  return page.locator('article.kanban-card').filter({
    has: page.getByRole('button', { name: title, exact: true }),
  });
}

test('Angular and TUI exchange one Hub-owned Kanban projection with conflict recovery', async ({
  page,
}) => {
  const fixture = record(
    JSON.parse(
      readFileSync(
        join(
          repositoryRoot,
          'tests',
          'fixtures',
          'kanban_model_dashboard',
          'kanban-model-dashboard.v1.json',
        ),
        'utf8',
      ),
    ) as unknown,
    'shared fixture',
  );
  const fixtureCard = record(fixture['card'], 'fixture.card');
  const fixtureError = record(fixture['error'], 'fixture.error');
  const fixtureErrorBody = record(fixtureError['body'], 'fixture.error.body');
  const fixtureErrorValue = record(
    fixtureErrorBody['error'],
    'fixture.error.body.error',
  );

  await page.addInitScript(
    ({ endpoint, accessToken }) => {
      localStorage.setItem('ananta.user.token', accessToken);
      localStorage.setItem('ananta.shell.mode', 'advanced');
      localStorage.setItem(
        'ananta.agents.v1',
        JSON.stringify([
          {
            name: 'hub',
            role: 'hub',
            url: endpoint,
            token: '',
          },
        ]),
      );
    },
    { endpoint: hubUrl, accessToken: token },
  );

  await page.goto('/board');
  await expect(page.getByTestId('kanban-board')).toBeVisible();
  await page
    .getByRole('button', { name: 'Snapshot aktualisieren', exact: true })
    .click();

  const title = String(fixtureCard['title']);
  const card = angularCard(page, title);
  await expect(card).toHaveCount(1);
  await expect(card).toContainText(String(fixtureCard['description']));
  await expect(card).toContainText(`Priorität ${String(fixtureCard['priority'])}`);
  await expect(card).toContainText(`Revision ${String(fixtureCard['revision'])}`);
  await expect(card).toContainText('In Arbeit');

  await card.getByRole('button', { name: 'Nach rechts' }).click();
  await expect(card).toContainText('Blockiert');
  await expect(card).toContainText('Revision 8');

  const tuiAfterAngular = runTuiProbe(
    'snapshot',
    '--task-id',
    String(fixtureCard['id']),
  );
  const tuiCardAfterAngular = record(
    tuiAfterAngular['card'],
    'TUI snapshot card',
  );
  expect(tuiAfterAngular['ok']).toBe(true);
  expect(tuiCardAfterAngular).toMatchObject({
    id: fixtureCard['id'],
    title: fixtureCard['title'],
    description: fixtureCard['description'],
    priority: fixtureCard['priority'],
    labels: fixtureCard['labels'],
    dependencies: fixtureCard['dependencies'],
    column_id: 'blocked',
    revision: 8,
  });

  const angularColumnTitles = await page
    .locator('.kanban-column > header h2')
    .allTextContents();
  const tuiColumns = tuiAfterAngular['columns'];
  expect(Array.isArray(tuiColumns)).toBe(true);
  expect(
    (tuiColumns as JsonRecord[]).map(column => String(column['title'])),
  ).toEqual(angularColumnTitles);

  const tuiMove = runTuiProbe(
    'move',
    '--task-id',
    String(fixtureCard['id']),
    '--expected-revision',
    '8',
    '--target-status',
    'todo',
    '--idempotency-key',
    'cross-surface-tui-resolve-r8',
  );
  expect(tuiMove['ok']).toBe(true);
  expect(record(tuiMove['card'], 'TUI move card')).toMatchObject({
    id: fixtureCard['id'],
    column_id: 'todo',
    revision: 9,
  });

  // The durable Hub replay updates Angular without a manual reload.
  await expect(card).toContainText('Offen', { timeout: 15_000 });
  await expect(card).toContainText('Revision 9');

  // Pause only the browser replay channel so Angular deliberately retains
  // revision 9 while the TUI creates revision 10.
  await page.route('**/api/v1/kanban/boards/*/events*', route => route.abort());
  const hiddenTuiMove = runTuiProbe(
    'move',
    '--task-id',
    String(fixtureCard['id']),
    '--expected-revision',
    '9',
    '--target-status',
    'in_progress',
    '--idempotency-key',
    'cross-surface-tui-progress-r9',
  );
  expect(hiddenTuiMove['ok']).toBe(true);
  expect(record(hiddenTuiMove['card'], 'hidden TUI move card')).toMatchObject({
    id: fixtureCard['id'],
    column_id: 'in_progress',
    revision: 10,
  });

  // This real Angular write uses revision 9, receives 409, discards its
  // optimistic view, and reloads the atomic snapshot at revision 10.
  await card.getByRole('button', { name: 'Nach rechts' }).click();
  await expect(card).toContainText('In Arbeit');
  await expect(card).toContainText('Revision 10');

  const staleTuiWrite = runTuiProbe(
    'move',
    '--task-id',
    String(fixtureCard['id']),
    '--expected-revision',
    '8',
    '--target-status',
    'completed',
    '--idempotency-key',
    'cross-surface-tui-stale-r8',
  );
  const staleError = record(staleTuiWrite['error'], 'TUI conflict error');
  expect(staleTuiWrite['ok']).toBe(false);
  expect(staleError['http_status']).toBe(fixtureError['http_status']);
  expect(staleError['code']).toBe(fixtureErrorValue['code']);
  expect(staleError['current_revision']).toBe(10);

  const finalTuiSnapshot = runTuiProbe(
    'snapshot',
    '--task-id',
    String(fixtureCard['id']),
  );
  expect(record(finalTuiSnapshot['card'], 'final TUI snapshot card')).toMatchObject({
    id: fixtureCard['id'],
    title: fixtureCard['title'],
    description: fixtureCard['description'],
    column_id: 'in_progress',
    revision: 10,
  });
});
