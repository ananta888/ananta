import { expect, test, type Page } from '@playwright/test';
import {
  HUB_URL,
  assertNoUnhandledBrowserErrors,
  createJourneyCleanupPolicy,
  loginFast,
} from './utils';

function unwrapList(body: any): any[] {
  if (Array.isArray(body)) return body;
  if (Array.isArray(body?.data)) return body.data;
  if (Array.isArray(body?.items)) return body.items;
  return [];
}

async function getHubInfo(page: Page): Promise<{ hubUrl: string; token: string | null }> {
  return page.evaluate((defaultHubUrl: string) => {
    const token = localStorage.getItem('ananta.user.token');
    const raw = localStorage.getItem('ananta.agents.v1');
    let hubUrl = defaultHubUrl;
    if (raw) {
      try {
        const agents = JSON.parse(raw);
        const hub = agents.find((a: any) => a.role === 'hub');
        if (hub?.url) hubUrl = hub.url;
      } catch {}
    }
    if (!hubUrl || hubUrl === 'undefined') hubUrl = defaultHubUrl;
    return { hubUrl, token };
  }, HUB_URL);
}

async function waitForConfigValue(
  request: any,
  hubUrl: string,
  headers: Record<string, string> | undefined,
  predicate: (cfg: any) => boolean,
  timeoutMs = 15000
) {
  const started = Date.now();
  while ((Date.now() - started) < timeoutMs) {
    const res = await request.get(`${hubUrl}/config`, { headers });
    if (res.ok()) {
      const body = await res.json();
      const cfg = body?.data || body;
      if (predicate(cfg)) return cfg;
    }
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error('Timed out waiting for expected /config value');
}

test.describe('Config + UI Control Hardening', () => {
  test('settings enforces validation and persists system+quality controls', async ({ page, request }) => {
    await loginFast(page, request);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const seed = await request.post(`${hubUrl}/config`, {
      headers,
      data: {
        http_timeout: 20,
        command_timeout: 25,
        agent_offline_timeout: 30,
        quality_gates: { enabled: true, autopilot_enforce: true, min_output_chars: 15 },
      },
    });
    expect(seed.ok()).toBeTruthy();

    await page.goto('/settings');
    const systemTab = page.locator('button.button-outline', { hasText: /^System$/i });
    const qualityTab = page.locator('button.button-outline', { hasText: /^Qualitaetsregeln$/i });
    await systemTab.click();

    const systemCard = page.locator('.card', { has: page.getByRole('heading', { name: /System Parameter/i }) });
    const systemSave = systemCard.getByRole('button', { name: /^Speichern$/i });
    const httpTimeout = page.locator('label:has-text("HTTP Timeout (s)") input[type="number"]');
    const commandTimeout = page.locator('label:has-text("Command Timeout (s)") input[type="number"]');
    const offlineTimeout = page.locator('label:has-text("Agent Offline Timeout (s)") input[type="number"]');

    await httpTimeout.fill('0');
    await expect(systemSave).toBeDisabled();
    await expect(systemCard.getByText(/Mindestens 1 Sekunde/i)).toBeVisible();

    await httpTimeout.fill('39');
    await commandTimeout.fill('44');
    await offlineTimeout.fill('45');
    await expect(systemSave).toBeEnabled();
    await systemSave.click();
      const systemSaveResponse = page.waitForResponse((res) =>
        res.url().includes('/config') &&
        res.request().method() === 'POST' &&
        res.status() === 200
      );
      await systemSave.click();
      await systemSaveResponse;
      await waitForConfigValue(
        request,
        hubUrl,
        headers,
        (cfg: any) =>
          Number(cfg?.http_timeout) === 39 &&
          Number(cfg?.command_timeout) === 44 &&
          Number(cfg?.agent_offline_timeout) === 45,
      30000
      );

    await qualityTab.click();
    const qualitySave = page.getByRole('button', { name: /Qualitaetsregeln speichern/i });
    const minChars = page.locator('label:has-text("Min. Output Zeichen") input[type="number"]');
    await minChars.fill('0');
    await expect(qualitySave).toBeDisabled();
    await minChars.fill('31');
    await qualitySave.click();
    await waitForConfigValue(
      request,
      hubUrl,
      headers,
      (cfg: any) => Number(cfg?.quality_gates?.min_output_chars) === 31,
      15000
    );

    await assertNoUnhandledBrowserErrors(page);
  });

  test('template creation controls in UI persist reliably to API', async ({ page, request }) => {
    await loginFast(page, request);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
    const cleanup = createJourneyCleanupPolicy(hubUrl, token, request);
    const templateNameA = `E2E Control Template A ${Date.now()}`;
    const templateNameB = `E2E Control Template B ${Date.now()}`;
    let templateIdA: string | null = null;
    let templateIdB: string | null = null;

    try {
      await page.goto('/templates');
      await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();
      await page.getByPlaceholder('Name').fill(templateNameA);
      await page.getByPlaceholder('Beschreibung').fill('created through UI control test A');
      await page.locator('textarea[placeholder*="Platzhalter"]').fill('Du bist {{agent_name}} und bearbeitest {{task_title}} fuer A.');
      await page.getByRole('button', { name: /Anlegen \/ Speichern/i }).click();

      await expect.poll(async () => {
        const listRes = await request.get(`${hubUrl}/templates`, { headers });
        if (!listRes.ok()) return '';
        const templates = unwrapList(await listRes.json());
        const found = templates.find((t: any) => t.name === templateNameA);
        templateIdA = found?.id || null;
        return String(found?.prompt_template || '');
      }, { timeout: 30000 }).toContain('fuer A');
      if (templateIdA) cleanup.trackTemplate(templateIdA);

      await page.getByPlaceholder('Name').fill(templateNameB);
      await page.getByPlaceholder('Beschreibung').fill('created through UI control test B');
      await page.locator('textarea[placeholder*="Platzhalter"]').fill('Du bist {{agent_name}} und bearbeitest {{task_title}} fuer B.');
      await page.getByRole('button', { name: /Anlegen \/ Speichern/i }).click();

      await expect.poll(async () => {
        const listRes = await request.get(`${hubUrl}/templates`, { headers });
        if (!listRes.ok()) return '';
        const templates = unwrapList(await listRes.json());
        const found = templates.find((t: any) => t.name === templateNameB);
        templateIdB = found?.id || null;
        return String(found?.description || '');
      }, { timeout: 30000 }).toContain('control test B');
      if (templateIdB) cleanup.trackTemplate(templateIdB);

      await assertNoUnhandledBrowserErrors(page);
    } finally {
      await cleanup.run();
    }
  });

  test('auto-planner config and advanced goal steering are end-to-end controllable', async ({ page, request }) => {
    await loginFast(page, request);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const seeded = await request.post(`${hubUrl}/tasks/auto-planner/configure`, {
      headers,
      data: {
        enabled: true,
        auto_followup_enabled: false,
        auto_start_autopilot: false,
        max_subtasks_per_goal: 6,
        default_priority: 'Medium',
        llm_timeout: 32,
      },
    });
    expect(seeded.ok()).toBeTruthy();

    let capturedGoalPayload: any = null;
    await page.route('**/goals', async route => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      capturedGoalPayload = route.request().postDataJSON();
      const id = `goal-hardening-${Date.now()}`;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            goal: { id, summary: capturedGoalPayload?.goal || 'Goal', status: 'planned' },
            subtask_count: 1,
            created_task_ids: [`task-${Date.now()}-1`],
            subtasks: [{ title: 'Analyse', priority: 'Medium' }],
            plan_id: `plan-${Date.now()}`,
          },
        }),
      });
    });
    await page.route('**/goals/*/detail*', async route => {
      const goalId = route.request().url().match(/\/goals\/([^/]+)\/detail/)?.[1] || 'goal';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            goal: { id: goalId, summary: 'Goal', status: 'planned' },
            trace: { trace_id: `trace-${goalId}` },
            artifacts: { result_summary: { completed_tasks: 0, failed_tasks: 0 }, headline_artifact: { preview: 'n/a' } },
            plan: { nodes: [] },
            governance: { policy: { total: 0, approved: 0, blocked: 0 }, verification: { total: 0, passed: 0, escalated: 0 } },
            tasks: [],
          },
        }),
      });
    });

    await page.goto('/auto-planner');

    await page.getByTestId('auto-planner-config-followups').check();
    await page.getByTestId('auto-planner-config-autostart').check();
    await page.getByRole('button', { name: /^Speichern$/i }).first().click();

    await expect.poll(async () => {
      const statusRes = await request.get(`${hubUrl}/tasks/auto-planner/status`, { headers });
      if (!statusRes.ok()) return '';
      const body = await statusRes.json();
      const status = body?.data || body;
      return `${Boolean(status?.auto_followup_enabled)}-${Boolean(status?.auto_start_autopilot)}`;
    }, { timeout: 20000 }).toBe('true-true');

    await page.getByTestId('goal-mode-toggle').click();
    await expect(page.getByTestId('goal-advanced-fields')).toBeVisible();
    await page.locator('label:has-text("Constraints") textarea').fill('Constraint A\nConstraint B');
    await page.locator('label:has-text("Acceptance Criteria") textarea').fill('AC 1\nAC 2');
    await page.locator('label:has-text("Sicherheitsniveau") select').selectOption('strict');
    await page.locator('label:has-text("Routing-Praeferenz") input').fill('active_team_or_hub_default');
    await page.getByTestId('auto-planner-goal-input').fill(`Hardening Goal ${Date.now()}`);
    await page.getByTestId('auto-planner-goal-plan').click();

    await expect(page.getByTestId('goal-submit-result')).toBeVisible({ timeout: 15000 });
    expect(Array.isArray(capturedGoalPayload?.constraints)).toBeTruthy();
    expect(capturedGoalPayload?.constraints).toEqual(['Constraint A', 'Constraint B']);
    expect(Array.isArray(capturedGoalPayload?.acceptance_criteria)).toBeTruthy();
    expect(capturedGoalPayload?.acceptance_criteria).toEqual(['AC 1', 'AC 2']);
    expect(capturedGoalPayload?.workflow?.policy?.security_level).toBe('strict');
    expect(capturedGoalPayload?.workflow?.routing?.mode).toBe('active_team_or_hub_default');

    await assertNoUnhandledBrowserErrors(page);
  });
});
