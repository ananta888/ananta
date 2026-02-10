import { test, expect } from '@playwright/test';
import { HUB_URL, login } from './utils';

test.describe('Teams CRUD', () => {
  async function getHubInfo(page: any) {
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

  test('create, update, activate, delete team via API', async ({ page, request }) => {
    await login(page);
    await page.goto('/teams');
    await expect(page.getByRole('heading', { name: /Management/i })).toBeVisible();

    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const name = `E2E Team ${Date.now()}`;
    const updatedDescription = 'E2E Team Beschreibung aktualisiert';

    const createRes = await request.post(`${hubUrl}/teams`, {
      headers,
      data: { name, description: 'E2E Team Beschreibung', members: [] }
    });
    expect(createRes.status()).toBe(201);
    const createBody = await createRes.json();
    const created = createBody?.data || createBody;
    expect(created?.id).toBeTruthy();

    const patchRes = await request.patch(`${hubUrl}/teams/${created.id}`, {
      headers,
      data: { description: updatedDescription }
    });
    expect(patchRes.ok()).toBeTruthy();

    const activateRes = await request.post(`${hubUrl}/teams/${created.id}/activate`, {
      headers,
      data: {}
    });
    expect(activateRes.ok()).toBeTruthy();

    const listRes = await request.get(`${hubUrl}/teams`, { headers });
    expect(listRes.ok()).toBeTruthy();
    const listBody = await listRes.json();
    const teams = Array.isArray(listBody) ? listBody : (listBody?.data || []);
    const found = teams.find((team: any) => team.id === created.id);
    expect(found).toBeTruthy();
    expect(found.description).toBe(updatedDescription);
    expect(found.is_active).toBeTruthy();

    const delRes = await request.delete(`${hubUrl}/teams/${created.id}`, { headers });
    expect(delRes.ok()).toBeTruthy();

    const afterRes = await request.get(`${hubUrl}/teams`, { headers });
    expect(afterRes.ok()).toBeTruthy();
    const afterBody = await afterRes.json();
    const afterTeams = Array.isArray(afterBody) ? afterBody : (afterBody?.data || []);
    expect(afterTeams.find((team: any) => team.id === created.id)).toBeFalsy();
  });

  test('delete missing team returns not found', async ({ page, request }) => {
    await login(page);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const missingId = `team-missing-${Date.now()}`;
    const res = await request.delete(`${hubUrl}/teams/${missingId}`, { headers });
    expect(res.status()).toBe(404);
  });
});

