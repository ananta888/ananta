import { expect, test, type Page } from '@playwright/test';

import { loginFast } from './utils';

test.describe.configure({ mode: 'serial', retries: 0 });

test.describe('live public Git source journey', () => {
  test.skip(
    process.env.RUN_PROJECT_SOURCE_LIVE_E2E !== '1',
    'Runs only against an explicitly selected live Ananta composition.',
  );

  test('registers, connects and refreshes a public GitHub repository', async ({
    page,
    request,
  }, testInfo) => {
    test.setTimeout(300_000);
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));

    await loginFast(page, request);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('app-root')).toBeVisible();

    if (new URL(page.url()).pathname !== '/projects') {
      await clickMainNavigation(page, 'Projekte');
    }
    await expect(page.getByRole('heading', { name: 'Projekte', exact: true })).toBeVisible();

    const projectName = `Public Git ${Date.now()}`;
    await page.getByLabel('Name', { exact: true }).fill(projectName);
    await page.getByLabel('Beschreibung (optional)').fill(
      'Live Playwright project for the credential-free public Git journey.',
    );
    const projectResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/projects',
    );
    await page.getByRole('button', { name: 'Projekt erstellen' }).click();
    const projectResponse = await projectResponsePromise;
    expect(projectResponse.status()).toBe(201);
    const projectEnvelope = unwrap(await projectResponse.json());
    const project = projectEnvelope?.project ?? projectEnvelope;
    const projectId = requireServerId(project?.id, 'project id');
    await expect(page.locator('#global-project-select')).toHaveValue(projectId);

    await clickMainNavigation(page, 'Quellen');
    await page.getByRole('link', { name: /Quelle hinzufügen|Erste Quelle hinzufügen/ }).first().click();
    await expect(page.getByRole('heading', { name: 'Quelle aufnehmen' })).toBeVisible();
    await page.getByRole('button', { name: /Registriertes Remote/ }).click();

    await expect(page.getByRole('heading', { name: 'Git-Autorisierung' })).toBeVisible();
    const providerStatus = (
      await page.locator('app-git-authorization-onboarding .health').textContent()
    )?.trim();
    await expect(page.locator('app-notifications .notification.error')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Öffentliches Git-Remote registrieren' })).toBeVisible();
    await page.getByTestId('public-github-owner').fill('octocat');
    await page.getByTestId('public-git-repository').fill('Hello-World');
    await page.getByTestId('public-git-ref').fill('master');

    const validationResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST'
        && new URL(response.url()).pathname === '/api/source-control/v1/public-remotes/validate',
      { timeout: 120_000 },
    );
    const registrationResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST'
        && new URL(response.url()).pathname === '/api/source-control/v1/public-remotes',
      { timeout: 120_000 },
    );
    await page.getByTestId('create-public-remote').click();
    const validationResponse = await validationResponsePromise;
    expect(validationResponse.ok()).toBeTruthy();
    const registrationResponse = await registrationResponsePromise;
    expect(registrationResponse.status()).toBe(201);
    const remote = unwrap(await registrationResponse.json());
    const remoteId = requireServerId(remote?.remote_id, 'public remote id');
    const commitSha = requireServerId(remote?.commit_sha, 'resolved public remote commit');
    await expect(page.getByText('Das öffentliche Remote wurde vom Hub registriert.')).toBeVisible();
    await expect(page.getByTestId('remote-catalog')).toHaveValue(remoteId, { timeout: 60_000 });

    await page.getByTestId('remote-display-name').fill(projectName);
    const connectionResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST'
        && new URL(response.url()).pathname === '/api/source-control/v1/connections',
      { timeout: 60_000 },
    );
    await page.getByTestId('submit-source').click();
    const connectionResponse = await connectionResponsePromise;
    expect(connectionResponse.status()).toBe(201);
    const connectionCreation = unwrap(await connectionResponse.json());
    const connectionId = requireServerId(
      connectionCreation?.connection?.connection_id,
      'public Git connection id',
    );
    await expect(page.getByTestId('content-admission-success')).toBeVisible();

    await page.getByRole('link', { name: /Source-Control-Uebersicht/ }).click();
    await page.getByRole('link', { name: 'Index-Journey' }).click();
    await expect(page.getByRole('heading', { name: 'Von der Quelle zum aktiven Index' })).toBeVisible();
    const connectionChoice = page.getByRole('listitem').filter({ hasText: connectionId });
    await expect(connectionChoice).toBeVisible({ timeout: 60_000 });
    await connectionChoice.click();

    const refresh = page.getByRole('button', { name: 'Quelle aktualisieren' });
    await expect(refresh).toBeEnabled({ timeout: 60_000 });
    const refreshResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST'
        && new URL(response.url()).pathname === `/api/source-control/v1/connections/${connectionId}/refresh`,
      { timeout: 120_000 },
    );
    await refresh.click();
    const refreshResponse = await refreshResponsePromise;
    expect(refreshResponse.status()).toBe(202);
    await expect(page.getByTestId('journey-scan')).toBeEnabled({ timeout: 120_000 });
    expect(pageErrors).toEqual([]);

    testInfo.annotations.push(
      {
        type: 'private-git-provider',
        description: providerStatus || 'No private-provider health text returned.',
      },
      { type: 'project-id', description: projectId },
      { type: 'public-remote-id', description: remoteId },
      { type: 'public-remote-commit', description: commitSha },
      { type: 'connection-id', description: connectionId },
    );
  });
});

function unwrap(payload: any): any {
  return payload?.data ?? payload;
}

function requireServerId(value: unknown, label: string): string {
  const id = String(value ?? '').trim();
  expect(id, `The Hub did not return a ${label}.`).not.toBe('');
  return id;
}

async function clickMainNavigation(page: Page, label: string): Promise<void> {
  const navigation = page.locator('.app-nav:visible');
  const group = navigation.locator('details').filter({ hasText: label }).first();
  if (!(await group.evaluate((element) => (element as HTMLDetailsElement).open))) {
    await group.locator('summary').click();
  }
  await group.getByText(label, { exact: true }).click();
}
