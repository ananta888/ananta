import { expect, type Page } from '@playwright/test';

export async function createProjectAndOpenJourney(
  page: Page,
  projectName: string,
  description: string,
): Promise<string> {
  // Deliberately not the project-scoped route: this helper is what creates
  // the first project, so it cannot require one to already exist.
  await page.goto('/projects', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('app-root')).toBeVisible();
  await clickMainNavigation(page, 'Projekte');
  await expect(page.getByRole('heading', { name: 'Projekte', exact: true })).toBeVisible();
  await expect(page.getByText('Quellen & Indexierung', { exact: true })).toBeVisible();
  await page.getByLabel('Name', { exact: true }).fill(projectName);
  await page.getByLabel('Beschreibung (optional)').fill(description);
  const responsePromise = page.waitForResponse(response =>
    response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/projects',
  );
  await page.getByRole('button', { name: 'Projekt erstellen' }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(201);
  const payload = unwrap(await response.json());
  const projectId = requireServerId(payload?.project?.id ?? payload?.id, 'project id');
  const row = page.locator('main.projects li').filter({ hasText: projectName });
  await expect(row.getByRole('button', { name: 'Git oder Ordner hinzufügen' })).toBeVisible();
  await row.getByRole('button', { name: 'Git oder Ordner hinzufügen' }).click();
  await expect(page.getByRole('heading', { name: 'Von der Quelle zum aktiven Index' })).toBeVisible();
  await expect(page.getByTestId('journey-project-binding')).toContainText(
    `Wird Projekt ${projectName} zugeordnet`,
  );
  expect(new URL(page.url()).searchParams.get('projectId')).toBe(projectId);
  return projectId;
}

export async function openSourceCard(page: Page, label: string): Promise<void> {
  const details = page.locator('details.source-card').filter({ has: page.locator('summary', { hasText: label }) });
  if (!(await details.evaluate(element => (element as HTMLDetailsElement).open))) {
    await details.locator('summary').click();
  }
}

export async function validateAndCreateJourneyConnection(
  page: Page,
  displayName: string,
): Promise<string> {
  await page.locator('#journey-name').fill(displayName);
  const validationPromise = page.waitForResponse(response =>
    response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/source-control/v1/connections/validate',
  );
  await page.getByTestId('journey-validate').click();
  expect((await validationPromise).ok()).toBeTruthy();
  await expect(page.getByTestId('journey-create')).toBeEnabled();
  const creationPromise = page.waitForResponse(response =>
    response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/source-control/v1/connections',
  );
  await page.getByTestId('journey-create').click();
  const creation = await creationPromise;
  expect(creation.status()).toBe(201);
  return requireServerId(
    unwrap(await creation.json())?.connection?.connection_id,
    'connection id',
  );
}

export async function refreshScanAndGrant(
  page: Page,
  connectionId: string,
): Promise<{ sourceRevisionId: string; destinationId: string; grantId: string }> {
  const refresh = page.getByRole('button', { name: 'Quelle aktualisieren' });
  await expect(refresh).toBeEnabled({ timeout: 60_000 });
  const refreshPromise = page.waitForResponse(response =>
    response.request().method() === 'POST'
      && new URL(response.url()).pathname === `/api/source-control/v1/connections/${connectionId}/refresh`,
  );
  await refresh.click();
  expect((await refreshPromise).ok()).toBeTruthy();
  const scan = page.getByTestId('journey-scan');
  await expect(scan).toBeEnabled({ timeout: 120_000 });
  const scanPromise = page.waitForResponse(response =>
    response.request().method() === 'POST'
      && new URL(response.url()).pathname === `/api/source-control/v1/connections/${connectionId}/scan`,
  );
  await scan.click();
  expect((await scanPromise).ok()).toBeTruthy();

  const prepare = page.getByTestId('prepare-index-access');
  await expect(prepare).toBeEnabled({ timeout: 120_000 });
  const preparationPromise = page.waitForResponse(response =>
    response.request().method() === 'GET'
      && new URL(response.url()).pathname
        === `/api/source-control/v1/connections/${connectionId}/actions/prepare-index-access`,
  );
  await prepare.click();
  const preparationResponse = await preparationPromise;
  expect(preparationResponse.ok()).toBeTruthy();
  const preparation = unwrap(await preparationResponse.json());
  await expect(page.getByTestId('index-access-destination')).not.toHaveValue('');
  await expect(page.getByTestId('index-access-option')).not.toHaveValue('');
  await expect(page.getByTestId('index-access-effect')).toContainText('local');
  await expect(page.getByTestId('index-access-effect')).toContainText('redacted');
  await page.getByTestId('index-access-confirmation').check();
  const grantPromise = page.waitForResponse(response =>
    response.request().method() === 'POST'
      && new URL(response.url()).pathname
        === `/api/source-control/v1/connections/${connectionId}/actions/prepare-index-access`,
  );
  await page.getByTestId('grant-index-access').click();
  const grantResponse = await grantPromise;
  expect(grantResponse.ok()).toBeTruthy();
  const result = unwrap(await grantResponse.json());
  await expect(page.getByTestId('index-access-success')).toBeVisible();
  return {
    sourceRevisionId: requireServerId(result?.source_revision_id ?? preparation?.source_revision?.source_revision_id, 'source revision id'),
    destinationId: requireServerId(result?.destination_id, 'destination id'),
    grantId: requireServerId(result?.grant?.grant_id, 'grant id'),
  };
}

export async function runAndActivate(
  page: Page,
  connectionId: string,
): Promise<string> {
  const profile = page.locator('#journey-profile');
  const profileId = await firstEnabledOptionValue(profile);
  await profile.selectOption(profileId);
  const start = page.getByTestId('journey-start-index');
  await expect(start).toBeEnabled({ timeout: 60_000 });
  const runPromise = page.waitForResponse(response =>
    response.request().method() === 'POST'
      && new URL(response.url()).pathname === `/api/source-control/v1/connections/${connectionId}/runs`,
  );
  await start.click();
  expect((await runPromise).ok()).toBeTruthy();
  const article = await waitForRunArticle(page);
  const indexId = requireServerId((await article.locator('strong').textContent())?.trim(), 'knowledge index id');
  const activate = article.getByRole('button', { name: 'Aktivieren' });
  await waitForIndexAction(page, activate);
  const activationPromise = page.waitForResponse(response =>
    response.request().method() === 'POST'
      && new URL(response.url()).pathname === `/api/source-control/v1/indices/${indexId}/activate`,
  );
  await activate.click();
  expect((await activationPromise).ok()).toBeTruthy();
  await expect(page.getByText('Index wurde serverseitig aktiviert.')).toBeVisible();
  return indexId;
}

export function unwrap(payload: any): any {
  return payload?.data ?? payload;
}

export function requireServerId(value: unknown, label: string): string {
  const id = String(value ?? '').trim();
  expect(id, `The Hub did not return a ${label}.`).not.toBe('');
  return id;
}

async function clickMainNavigation(page: Page, label: string): Promise<void> {
  const navigation = page.locator('.app-nav:visible');
  const group = navigation.locator('details').filter({ hasText: label }).first();
  if (!(await group.evaluate(element => (element as HTMLDetailsElement).open))) {
    await group.locator('summary').click();
  }
  await group.getByText(label, { exact: true }).click();
}

async function firstEnabledOptionValue(select: ReturnType<Page['locator']>): Promise<string> {
  const value = await select.locator('option:not([value=""]):not(:disabled)').first().getAttribute('value');
  return requireServerId(value, 'profile id');
}

async function waitForIndexAction(page: Page, action: ReturnType<Page['locator']>): Promise<void> {
  const refresh = page.getByRole('button', { name: 'Läufe aktualisieren' });
  for (let attempt = 0; attempt < 300; attempt += 1) {
    if (await action.isEnabled().catch(() => false)) return;
    if (await refresh.isEnabled().catch(() => false)) await refresh.click();
    await page.waitForTimeout(2_000);
  }
  await expect(action).toBeEnabled();
}

async function waitForRunArticle(page: Page): Promise<ReturnType<Page['locator']>> {
  const article = page.locator('.run-list article').first();
  const refresh = page.getByRole('button', { name: 'Läufe aktualisieren' });
  for (let attempt = 0; attempt < 300; attempt += 1) {
    if (await article.isVisible().catch(() => false)) return article;
    if (await refresh.isEnabled().catch(() => false)) await refresh.click();
    await page.waitForTimeout(2_000);
  }
  await expect(article).toBeVisible();
  return article;
}
