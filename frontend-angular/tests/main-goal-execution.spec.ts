import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import {
  HUB_URL,
  ALPHA_URL,
  BETA_URL,
  ALPHA_AGENT_TOKEN,
  BETA_AGENT_TOKEN,
  assertNoUnhandledBrowserErrors,
  assertErrorOverlaysInViewport,
  createJourneyCleanupPolicy,
  loginFast,
} from './utils';

type HubInfo = { hubUrl: string; token: string | null };

function unwrap<T = any>(body: any): T {
  if (body && typeof body === 'object' && 'data' in body) return body.data as T;
  return body as T;
}

async function getHubInfo(page: Page): Promise<HubInfo> {
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

async function apiCall(
  request: APIRequestContext,
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  url: string,
  token: string | null,
  data?: any
) {
  const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
  if (method === 'GET') return request.get(url, { headers });
  if (method === 'POST') return request.post(url, { headers, data, timeout: 45_000 });
  if (method === 'PATCH') return request.patch(url, { headers, data, timeout: 30_000 });
  return request.delete(url, { headers, timeout: 30_000 });
}

async function ensureWorkersRegistered(request: APIRequestContext, hubUrl: string, token: string) {
  const workers = [
    { name: 'alpha', url: ALPHA_URL, token: ALPHA_AGENT_TOKEN, role: 'coder' },
    { name: 'beta', url: BETA_URL, token: BETA_AGENT_TOKEN, role: 'reviewer' },
  ];
  for (const worker of workers) {
    const reg = await request.post(`${hubUrl}/register`, {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        name: worker.name,
        url: worker.url,
        role: 'worker',
        token: worker.token,
        worker_roles: [worker.role],
      },
    });
    expect(reg.ok(), `worker register failed for ${worker.name}: ${reg.status()}`).toBeTruthy();
  }
}

test.describe('Main Goal Execution Journey', () => {
  test('runs goal-derived tasks with two workers and exposes progress in UI', async ({ page, request }) => {
    test.setTimeout(300_000);
    await loginFast(page, request);
    const { hubUrl, token } = await getHubInfo(page);
    expect(token).toBeTruthy();
    const authToken = token as string;
    const cleanup = createJourneyCleanupPolicy(hubUrl, authToken, request);

    let createdTeamId: string | null = null;
    const createdTaskIds: string[] = [];

    try {
      await ensureWorkersRegistered(request, hubUrl, authToken);

      const typesRes = await apiCall(request, 'GET', `${hubUrl}/teams/types`, authToken);
      expect(typesRes.ok()).toBeTruthy();
      const types = unwrap<any[]>(await typesRes.json()) || [];
      const scrum = types.find((item: any) => String(item?.name || '').toLowerCase() === 'scrum');
      expect(scrum?.id).toBeTruthy();

      const rolesRes = await apiCall(request, 'GET', `${hubUrl}/teams/roles`, authToken);
      expect(rolesRes.ok()).toBeTruthy();
      const roles = unwrap<any[]>(await rolesRes.json()) || [];
      const devRole = roles.find((r: any) => String(r?.name || '').toLowerCase() === 'developer') || roles[0];
      const poRole = roles.find((r: any) => String(r?.name || '').toLowerCase() === 'product owner') || roles[1] || roles[0];
      expect(devRole?.id).toBeTruthy();
      expect(poRole?.id).toBeTruthy();

      const teamName = `E2E Execution Team ${Date.now()}`;
      const teamCreate = await apiCall(request, 'POST', `${hubUrl}/teams`, authToken, {
        name: teamName,
        description: 'E2E execution journey with two workers',
        team_type_id: scrum.id,
        members: [
          { agent_url: ALPHA_URL, role_id: devRole.id },
          { agent_url: BETA_URL, role_id: poRole.id },
        ],
      });
      expect(teamCreate.status(), `POST /teams failed with ${teamCreate.status()}`).toBe(201);
      const createdTeam = unwrap<any>(await teamCreate.json());
      createdTeamId = createdTeam?.id || null;
      expect(createdTeamId).toBeTruthy();
      cleanup.trackTeam(createdTeamId);

      const activateRes = await apiCall(request, 'POST', `${hubUrl}/teams/${createdTeamId}/activate`, authToken, {});
      expect(activateRes.ok()).toBeTruthy();

      const plannerCfg = await apiCall(request, 'POST', `${hubUrl}/tasks/auto-planner/configure`, authToken, {
        enabled: true,
        auto_followup_enabled: false,
        auto_start_autopilot: false,
        max_subtasks_per_goal: 2,
        llm_timeout: 45,
      });
      expect(plannerCfg.ok()).toBeTruthy();

      const goalText = 'Erstelle eine kleine API plus UI Aufgabenstruktur und liefere ein Ergebnisartefakt.';
      let planRes = await apiCall(request, 'POST', `${hubUrl}/tasks/auto-planner/plan`, authToken, {
        goal: goalText,
        team_id: createdTeamId,
        create_tasks: true,
        use_repo_context: false,
        use_template: false,
      });

      if (planRes.status() !== 201) {
        planRes = await apiCall(request, 'POST', `${hubUrl}/tasks/auto-planner/plan`, authToken, {
          goal: goalText,
          team_id: createdTeamId,
          create_tasks: true,
          use_repo_context: false,
          use_template: true,
        });
      }
      expect(planRes.status(), `planner failed with ${planRes.status()}`).toBe(201);
      const planData = unwrap<any>(await planRes.json()) || {};
      createdTaskIds.push(...(Array.isArray(planData.created_task_ids) ? planData.created_task_ids : []));
      if (createdTaskIds.length === 0) {
        const fallbackTasks = [
          `E2E Exec Task A ${Date.now()}`,
          `E2E Exec Task B ${Date.now()}`,
        ];
        for (const title of fallbackTasks) {
          const createTaskRes = await apiCall(request, 'POST', `${hubUrl}/tasks`, authToken, {
            title,
            status: 'todo',
            team_id: createdTeamId,
          });
          expect(createTaskRes.ok(), `fallback task create failed: ${createTaskRes.status()}`).toBeTruthy();
          const createdTask = unwrap<any>(await createTaskRes.json());
          if (createdTask?.id) createdTaskIds.push(createdTask.id);
        }
      }
      expect(createdTaskIds.length).toBeGreaterThanOrEqual(1);
      cleanup.trackTasks(createdTaskIds);

      for (const taskId of createdTaskIds) {
        const normalizeRes = await apiCall(request, 'PATCH', `${hubUrl}/tasks/${taskId}`, authToken, {
          status: 'todo',
          depends_on: [],
        });
        expect(normalizeRes.ok()).toBeTruthy();
      }

      const assignedWorkers = new Set<string>();
      const terminalStates = new Set(['completed', 'failed']);
      let sawTerminal = false;

      for (let i = 0; i < 6; i += 1) {
        const tickRes = await apiCall(request, 'POST', `${hubUrl}/tasks/autopilot/tick`, authToken, {
          team_id: createdTeamId,
        });
        expect(tickRes.ok()).toBeTruthy();

        for (const taskId of createdTaskIds) {
          const taskRes = await apiCall(request, 'GET', `${hubUrl}/tasks/${taskId}`, authToken);
          expect(taskRes.ok()).toBeTruthy();
          const task = unwrap<any>(await taskRes.json());
          if (task?.assigned_agent_url) assignedWorkers.add(String(task.assigned_agent_url));
          if (terminalStates.has(String(task?.status || ''))) sawTerminal = true;
        }
        if (sawTerminal || assignedWorkers.size > 0) break;
        await page.waitForTimeout(1200);
      }

      expect(assignedWorkers.size, 'at least one worker assignment expected').toBeGreaterThan(0);
      for (const workerUrl of assignedWorkers) {
        expect([ALPHA_URL, BETA_URL]).toContain(workerUrl);
      }
      expect(sawTerminal, 'at least one task should reach terminal state in observation window').toBeTruthy();

      const inspectTaskId = createdTaskIds[0];
      await page.goto(`/task/${inspectTaskId}`);
      await expect(page.getByRole('heading', { name: /Task/i })).toBeVisible();
      await page.getByRole('button', { name: /Details/i }).click();
      await expect(page.locator('text=/Status|Task/i').first()).toBeVisible();

      await assertErrorOverlaysInViewport(page);
      await assertNoUnhandledBrowserErrors(page);
    } finally {
      await cleanup.run();
    }
  });
});
