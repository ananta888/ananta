import { test, expect } from '@playwright/test';

const BASE_AGENT = 'default';

// Health endpoint
test('health check', async ({ request }) => {
  const res = await request.get('/health');
  expect(res.status()).toBe(200);
  expect(await res.json()).toEqual({ status: 'ok' });
});

// next-config endpoint
test('next-config structure', async ({ request }) => {
  const res = await request.get('/next-config');
  expect(res.status()).toBe(200);
  const data = await res.json();
  expect(data).toHaveProperty('agent');
  expect(data).toHaveProperty('api_endpoints');
  expect(data).toHaveProperty('prompt_templates');
});

// approve endpoint idempotence
test('approve idempotent', async ({ request }) => {
  const payload = { agent: BASE_AGENT, task: 't', response: 'r' };
  const first = await request.post('/approve', { data: payload });
  expect(first.status()).toBe(200);
  const second = await request.post('/approve', { data: payload });
  expect(second.status()).toBe(200);
});

// simple agent workflow simulation
test('agent workflow simulation', async ({ request }) => {
  const cfg = await (await request.get('/next-config')).json();
  const agent = cfg.agent;
  const taskRes = await request.get(`/tasks/next?agent=${agent}`);
  expect(taskRes.status()).toBe(200);
  const taskData = await taskRes.json();
  const task = taskData.task || 'demo-task';
  const approveRes = await request.post('/approve', {
    data: { agent, task, response: 'ok' },
  });
  expect(approveRes.status()).toBe(200);
  const status = await request.get(`/agent/${agent}/tasks`);
  expect(status.status()).toBe(200);
});
