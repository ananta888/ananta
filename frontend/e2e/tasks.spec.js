import { test, expect } from '@playwright/test';

// Use Playwright baseURL for controller via request context; only configure agentUrl for direct agent calls inside Docker
const agentUrl = process.env.AGENT_URL || 'http://ai-agent:5000';

test('task creation via UI persists to DB and is processed by ai-agent', async ({ page, request }) => {
  const task = `e2e-task-${Date.now()}`;
  const agent = 'Architect';

  await page.goto('/ui/');
  await page.waitForLoadState('networkidle');
  await page.click('text=Tasks');
  await page.fill('input[placeholder="Task"]', task);
  await page.fill('input[placeholder="Agent (optional)"]', agent);
  await page.click('text=Add');

  const beforeResp = await request.get(`/agent/${encodeURIComponent(agent)}/tasks`);
  expect(beforeResp.ok()).toBeTruthy();
  const beforeData = await beforeResp.json();
  expect(beforeData.tasks.some(t => t.task === task)).toBe(true);

  let found = false;
  for (let i = 0; i < 20; i++) {
    const logResp = await request.get(`${agentUrl}/agent/${encodeURIComponent(agent)}/log`);
    if (logResp.ok()) {
      const text = await logResp.text();
      if (text.includes(task)) {
        found = true;
        break;
      }
    }
    await new Promise(r => setTimeout(r, 1000));
  }
  expect(found).toBe(true);

  const afterResp = await request.get(`/agent/${encodeURIComponent(agent)}/tasks`);
  expect(afterResp.ok()).toBeTruthy();
  const afterData = await afterResp.json();
  expect(afterData.tasks.some(t => t.task === task)).toBe(false);
});
