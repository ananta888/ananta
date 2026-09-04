import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

import { HUB_URL, loginFast } from './utils';

test('native workspace persists room, thread and cursor across a browser reconnect', async ({ page, request }) => {
  test.setTimeout(120_000);
  await loginFast(page, request);
  await page.goto('/collaboration');
  await expect(page.getByRole('heading', { name: 'Collaboration Workspaces' })).toBeVisible();

  const suffix = Date.now().toString(36);
  const workspaceTitle = `Native workspace ${suffix}`;
  const roomTitle = `Pair room ${suffix}`;
  const message = `Durable message ${suffix}`;
  const reply = `Thread reply ${suffix}`;

  await page.getByPlaceholder('Workspace-Titel').fill(workspaceTitle);
  const workspaceResponse = page.waitForResponse(response =>
    response.url() === `${HUB_URL}/api/collaboration/workspaces`
      && response.request().method() === 'POST'
      && response.status() === 201,
  );
  await page.getByRole('button', { name: 'Erstellen' }).click();
  const workspace = (await (await workspaceResponse).json()).data;
  await expect(page.getByRole('button', { name: workspaceTitle })).toBeVisible();

  await page.getByPlaceholder('Room-Titel').fill(roomTitle);
  const roomResponse = page.waitForResponse(response =>
    response.url().endsWith(`/api/collaboration/workspaces/${workspace.workspace_id}/rooms`)
      && response.request().method() === 'POST'
      && response.status() === 201,
  );
  await page.getByRole('button', { name: 'Room anlegen' }).click();
  const room = (await (await roomResponse).json()).data;
  await expect(page.getByText(roomTitle, { exact: true })).toBeVisible();

  await page.getByPlaceholder('Nachricht').fill(message);
  const messageResponse = page.waitForResponse(response =>
    response.url().endsWith(`/api/collaboration/workspaces/${workspace.workspace_id}/events`)
      && response.request().method() === 'POST'
      && response.status() === 201,
  );
  await page.getByRole('button', { name: 'Senden', exact: true }).click();
  const event = (await (await messageResponse).json()).data;
  await expect(page.getByText(message, { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Thread öffnen' }).click();
  await expect(page.getByRole('region', { name: 'Thread' })).toContainText(message);
  await page.getByPlaceholder('Antwort').fill(reply);
  await page.getByRole('button', { name: 'Antworten' }).click();
  await expect(page.getByRole('region', { name: 'Thread' })).toContainText(reply);

  const session = await page.evaluate(() => ({
    token: localStorage.getItem('ananta.user.token') || '',
  }));
  const workspaceDetailResponse = await request.get(
    `${HUB_URL}/api/collaboration/workspaces/${workspace.workspace_id}`,
    { headers: { Authorization: `Bearer ${session.token}` } },
  );
  expect(workspaceDetailResponse.status()).toBe(200);
  const workspaceDetail = (await workspaceDetailResponse.json()).data;
  const terminalOfferResponse = await request.post(
    `${HUB_URL}/api/collaboration/workspaces/${workspace.workspace_id}/resource-offers`,
    {
      headers: { Authorization: `Bearer ${session.token}` },
      data: {
        schema: 'ananta.collaboration-resource-offer.v1',
        offer_id: `terminal-${suffix}`,
        workspace_id: workspace.workspace_id,
        owner_actor_binding_id: workspaceDetail.current_actor_binding_id,
        resource_id: `terminal-${suffix}`,
        capability_category: 'terminal',
        capacity_class: 'medium',
        scopes: ['task:execute'],
        expires_at: (Date.now() / 1000) + 300,
        sensitivity: 'workspace',
        attestation_status: 'unverified',
        metadata: { label: 'E2E terminal projection' },
      },
    },
  );
  expect(terminalOfferResponse.status()).toBe(201);
  const cursorResponse = await request.put(
    `${HUB_URL}/api/collaboration/workspaces/${workspace.workspace_id}/rooms/${room.room_id}/cursor`,
    {
      headers: { Authorization: `Bearer ${session.token}` },
      data: { sequence: event.sequence },
    },
  );
  expect(cursorResponse.status()).toBe(200);
  expect((await cursorResponse.json()).data.sequence).toBe(event.sequence);

  await page.getByRole('button', { name: 'Archivieren' }).click();
  await expect(page.getByRole('button', { name: 'Wieder öffnen' })).toBeVisible();
  await page.getByRole('button', { name: 'Wieder öffnen' }).click();
  await expect(page.getByRole('button', { name: 'Archivieren' })).toBeVisible();

  await page.reload();
  await page.getByRole('button', { name: workspaceTitle }).click();
  await page.getByRole('button', { name: new RegExp(roomTitle) }).click();
  await expect(page.getByText(message, { exact: true })).toBeVisible();
  await expect(page.getByText(reply, { exact: true })).toBeVisible();
  const projections = page.getByRole('region', { name: 'Terminal resource projections' });
  await expect(projections).toContainText(`terminal-${suffix} · medium`);
  await expect(projections).toContainText('Attestation unverified');
  await expect(page.getByRole('region', { name: 'Task projections' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Git projections' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Review and artifact projections' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'CodeCompass projections' })).toBeVisible();
  const accessibility = await new AxeBuilder({ page })
    .include('main')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(accessibility.violations).toEqual([]);
});
