import { test, expect, APIRequestContext, Page } from '@playwright/test';
import { HUB_URL, ALPHA_URL, BETA_URL, login } from './utils';

type HubInfo = { hubUrl: string; token: string };

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function unwrap<T = any>(body: any): T {
  if (body && typeof body === 'object' && 'data' in body) {
    return body.data as T;
  }
  return body as T;
}

async function getHubInfo(page: Page): Promise<HubInfo> {
  const data = await page.evaluate((defaultHubUrl: string) => {
    const token = localStorage.getItem('ananta.user.token') || '';
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

  expect(data.token, 'Auth token from login must exist').toBeTruthy();
  return data;
}

async function apiJson(request: APIRequestContext, method: 'GET' | 'POST' | 'PATCH', url: string, token: string, data?: any) {
  const res = method === 'GET'
    ? await request.get(url, { headers: { Authorization: `Bearer ${token}` } })
    : method === 'POST'
      ? await request.post(url, { headers: { Authorization: `Bearer ${token}` }, data })
      : await request.patch(url, { headers: { Authorization: `Bearer ${token}` }, data });
  const body = await res.json().catch(() => ({}));
  return { res, body };
}

test.describe('First Goal E2E', () => {
  test('uses local LLM, creates subtasks, assigns to team members, and monitors execution', async ({ page, request }) => {
    test.setTimeout(240_000);
    await login(page);
    const { hubUrl, token } = await getHubInfo(page);

    // 1) LLM default config -> local LMStudio.
    const lmstudioBaseUrl = process.env.E2E_LMSTUDIO_URL || 'http://192.168.96.1:1234/v1';
    const cfgGet = await apiJson(request, 'GET', `${hubUrl}/config`, token);
    expect(cfgGet.res.ok(), `GET /config failed: ${JSON.stringify(cfgGet.body)}`).toBeTruthy();
    const cfg = unwrap<any>(cfgGet.body) || {};
    cfg.llm_config = {
      ...(cfg.llm_config || {}),
      provider: 'lmstudio',
      model: cfg.llm_config?.model || 'lfm2.5-1.2b-glm-4.7-flash-thinking-i1',
      base_url: lmstudioBaseUrl
    };
    const cfgSet = await apiJson(request, 'POST', `${hubUrl}/config`, token, cfg);
    expect(cfgSet.res.ok(), `POST /config failed: ${JSON.stringify(cfgSet.body)}`).toBeTruthy();

    // 2) Verify local LLM is actually used.
    const llmCheck = await apiJson(
      request,
      'POST',
      `${hubUrl}/llm/generate`,
      token,
      { prompt: 'Antworte nur mit: LLM_LOCAL_OK' }
    );
    expect(llmCheck.res.ok(), `POST /llm/generate failed: ${JSON.stringify(llmCheck.body)}`).toBeTruthy();
    const llmData = unwrap<any>(llmCheck.body) || {};
    expect(String(llmData.response || ''), 'LLM response should include marker').toContain('LLM_LOCAL_OK');
    expect(llmData.routing?.effective?.provider, 'LLM provider should be lmstudio').toBe('lmstudio');
    expect(llmData.routing?.effective?.base_url, 'LLM base URL should be local LMStudio').toBe(lmstudioBaseUrl);

    // 3) Build/activate a small team with role mapping to workers.
    const typesRes = await apiJson(request, 'GET', `${hubUrl}/teams/types`, token);
    expect(typesRes.res.ok(), `GET /teams/types failed: ${JSON.stringify(typesRes.body)}`).toBeTruthy();
    const types = unwrap<any[]>(typesRes.body) || [];
    const scrum = types.find((t: any) => (t.name || '').toLowerCase() === 'scrum');
    expect(scrum?.id, 'Scrum team type must exist').toBeTruthy();

    const rolesRes = await apiJson(request, 'GET', `${hubUrl}/teams/roles`, token);
    expect(rolesRes.res.ok(), `GET /teams/roles failed: ${JSON.stringify(rolesRes.body)}`).toBeTruthy();
    const roles = unwrap<any[]>(rolesRes.body) || [];
    const devRole = roles.find((r: any) => (r.name || '').toLowerCase() === 'developer') || roles[0];
    const poRole = roles.find((r: any) => (r.name || '').toLowerCase() === 'product owner') || roles[1] || roles[0];
    expect(devRole?.id, 'Developer role missing').toBeTruthy();
    expect(poRole?.id, 'Product Owner role missing').toBeTruthy();

    const agentsRes = await apiJson(request, 'GET', `${hubUrl}/api/system/agents`, token);
    expect(agentsRes.res.ok(), `GET /api/system/agents failed: ${JSON.stringify(agentsRes.body)}`).toBeTruthy();
    const agents = unwrap<any>(agentsRes.body);
    const normalizeAgents = Array.isArray(agents)
      ? agents
      : Object.entries(agents || {}).map(([name, value]: [string, any]) => ({ name, ...(value || {}) }));
    const alphaAgent = normalizeAgents.find((a: any) => (a.name || '').toLowerCase() === 'alpha');
    const betaAgent = normalizeAgents.find((a: any) => (a.name || '').toLowerCase() === 'beta');
    expect(alphaAgent?.url, 'alpha agent URL missing in hub registry').toBeTruthy();
    expect(betaAgent?.url, 'beta agent URL missing in hub registry').toBeTruthy();

    const teamName = `E2E First Goal ${Date.now()}`;
    const teamCreate = await apiJson(request, 'POST', `${hubUrl}/teams`, token, {
      name: teamName,
      description: 'E2E: local LLM + auto planner + autopilot',
      team_type_id: scrum.id,
      members: [
        { agent_url: alphaAgent.url, role_id: devRole.id },
        { agent_url: betaAgent.url, role_id: poRole.id }
      ]
    });
    expect(teamCreate.res.status(), `POST /teams failed: ${JSON.stringify(teamCreate.body)}`).toBe(201);
    const team = unwrap<any>(teamCreate.body);
    expect(team?.id).toBeTruthy();

    const teamActivate = await apiJson(request, 'POST', `${hubUrl}/teams/${team.id}/activate`, token, {});
    expect(teamActivate.res.ok(), `POST /teams/{id}/activate failed: ${JSON.stringify(teamActivate.body)}`).toBeTruthy();

    // 4) Planner setup + requested initial goal.
    const plannerCfg = await apiJson(request, 'POST', `${hubUrl}/tasks/auto-planner/configure`, token, {
      enabled: true,
      auto_followup_enabled: false,
      auto_start_autopilot: false,
      max_subtasks_per_goal: 3
    });
    expect(plannerCfg.res.ok(), `POST /tasks/auto-planner/configure failed: ${JSON.stringify(plannerCfg.body)}`).toBeTruthy();

    const goal =
      'Erstelle eine einfache VWL Simulation mit Python als Backend und Angular als Frontend';
    const llmPlanPreview = await apiJson(request, 'POST', `${hubUrl}/tasks/auto-planner/plan`, token, {
      goal,
      team_id: team.id,
      create_tasks: false,
      use_template: false,
      use_repo_context: false
    });
    expect(llmPlanPreview.res.status(), `POST /tasks/auto-planner/plan (preview) failed: ${JSON.stringify(llmPlanPreview.body)}`).toBe(201);
    const llmPlanPreviewData = unwrap<any>(llmPlanPreview.body) || {};
    expect(Array.isArray(llmPlanPreviewData.subtasks), 'Preview subtasks should be an array').toBeTruthy();
    expect(llmPlanPreviewData.subtasks.length, 'Preview should contain LLM-generated subtasks').toBeGreaterThanOrEqual(2);
    expect(String(llmPlanPreviewData.raw_response || ''), 'Preview should include raw LLM response').not.toEqual('');

    const planRes = await apiJson(request, 'POST', `${hubUrl}/tasks/auto-planner/plan`, token, {
      goal,
      team_id: team.id,
      create_tasks: true,
      use_template: false,
      use_repo_context: false
    });
    expect(planRes.res.status(), `POST /tasks/auto-planner/plan failed: ${JSON.stringify(planRes.body)}`).toBe(201);
    const planData = unwrap<any>(planRes.body) || {};
    const createdTaskIds: string[] = planData.created_task_ids || [];
    expect(createdTaskIds.length, 'Planner should create multiple subtasks').toBeGreaterThanOrEqual(2);

    // 5) Ensure workers are marked online in hub registry.
    for (const worker of [alphaAgent, betaAgent]) {
      const reg = await request.post(`${hubUrl}/register`, {
        data: {
          name: worker.name,
          url: worker.url,
          role: 'worker',
          token: worker.token
        }
      });
      expect(reg.ok(), `POST /register failed for ${worker.name}`).toBeTruthy();
    }

    // 6) Manual autopilot ticks + monitoring.
    const assignedWorkers = new Set<string>();
    const terminalStates = new Set(['completed', 'failed']);
    let finalSnapshot: any[] = [];
    let terminalCount = 0;
    let dispatchedTotal = 0;

    for (let i = 0; i < 8; i += 1) {
      const tickRes = await apiJson(request, 'POST', `${hubUrl}/tasks/autopilot/tick`, token, {});
      expect(tickRes.res.ok(), `POST /tasks/autopilot/tick failed: ${JSON.stringify(tickRes.body)}`).toBeTruthy();
      const tickData = unwrap<any>(tickRes.body) || {};
      dispatchedTotal += Number(tickData.dispatched || 0);

      finalSnapshot = [];
      terminalCount = 0;
      for (const taskId of createdTaskIds) {
        const taskRes = await apiJson(request, 'GET', `${hubUrl}/tasks/${taskId}`, token);
        expect(taskRes.res.ok(), `GET /tasks/${taskId} failed: ${JSON.stringify(taskRes.body)}`).toBeTruthy();
        const task = unwrap<any>(taskRes.body);
        finalSnapshot.push(task);
        if (task?.assigned_agent_url) {
          assignedWorkers.add(String(task.assigned_agent_url));
        }
        if (terminalStates.has(String(task?.status || ''))) {
          terminalCount += 1;
        }
      }

      if (terminalCount > 0 || dispatchedTotal > 0) break;
      await sleep(2000);
    }

    // Ensure assignments happened on our team worker URLs.
    expect(assignedWorkers.size, 'At least one worker should be assigned').toBeGreaterThan(0);
    for (const worker of assignedWorkers) {
      expect([alphaAgent.url, betaAgent.url], `Unexpected assigned worker: ${worker}`).toContain(worker);
    }

    expect(
      dispatchedTotal > 0 || terminalCount > 0,
      'Autopilot should dispatch at least one LLM-generated subtask in monitoring window'
    ).toBeTruthy();
    for (const t of finalSnapshot) {
      expect(t?.team_id, `Task ${t?.id} must stay in created team context`).toBe(team.id);
    }
  });
});
