import { expect, test, type Page, type TestInfo } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { loginFast } from './utils';

test.describe.configure({ mode: 'serial', retries: 0 });

test.describe('live project, source and snapshot journey', () => {
  test.skip(
    process.env.RUN_PROJECT_SOURCE_LIVE_E2E !== '1',
    'Runs only against an explicitly selected live Ananta composition.',
  );

  test('creates a project and drives a browser folder to an active source index', async ({
    page,
    request,
  }, testInfo) => {
    test.setTimeout(1_200_000);
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));

    await loginFast(page, request);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('app-root')).toBeVisible();

    if (new URL(page.url()).pathname !== '/projects') {
      await clickMainNavigation(page, 'Projekte');
    }
    await expect(page.getByRole('heading', { name: 'Projekte', exact: true })).toBeVisible();

    const projectName = `Browser Snapshot ${Date.now()}`;
    await page.getByLabel('Name', { exact: true }).fill(projectName);
    await page.getByLabel('Beschreibung (optional)').fill(
      'Live Playwright project for the workspace snapshot journey.',
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
    await expect(
      page.getByLabel('Verfuegbare Projekte').getByText(projectName, { exact: true }),
    ).toBeVisible();

    await clickMainNavigation(page, 'Quellen');
    await expect(page.getByRole('heading', { name: 'Quellen', exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.get('projectId')).toBe(projectId);
    await expect(page.locator('#global-project-select')).toHaveValue(projectId);

    await clickMainNavigation(page, 'Projekte');
    await expect(page.getByRole('heading', { name: 'Projekte', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Ausgewaehlt' })).toBeDisabled();
    await clickMainNavigation(page, 'Quellen');
    await expect(page.getByRole('heading', { name: 'Quellen', exact: true })).toBeVisible();

    await page.getByRole('link', { name: /Quelle hinzufügen|Erste Quelle hinzufügen/ }).first().click();
    await expect(page.getByRole('heading', { name: 'Quelle aufnehmen' })).toBeVisible();

    await page.getByRole('button', { name: /Registriertes Remote/ }).click();
    await expect(page.getByRole('heading', { name: 'Git-Autorisierung' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Öffentliches Git-Remote registrieren' })).toBeVisible();
    await page.getByTestId('public-github-owner').fill('openai');
    await page.getByTestId('public-git-repository').fill('openai-cookbook');
    await page.getByTestId('public-git-ref').fill('main');
    await expect(page.getByTestId('create-public-remote')).toBeEnabled();
    const providerStatus = (await page.locator('app-git-authorization-onboarding .health').textContent())?.trim();
    testInfo.annotations.push({
      type: 'private-git-provider',
      description: providerStatus || 'No private-provider health text returned.',
    });

    await page.getByRole('button', { name: /Registrierter Workspace/ }).click();
    await expect(page.getByRole('heading', { name: 'Workspace-Snapshot hochladen' })).toBeVisible();
    const uploadDirectory = createUploadDirectory(testInfo);
    await page.getByTestId('workspace-snapshot-folder').setInputFiles(uploadDirectory);
    await expect(page.getByText(/2 Dateien,/)).toBeVisible();
    await page.getByTestId('workspace-snapshot-name').fill(projectName);

    const snapshotResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST'
        && new URL(response.url()).pathname === '/api/source-control/v1/workspace-snapshots',
      { timeout: 60_000 },
    );
    await page.getByTestId('upload-workspace-snapshot').click();
    const snapshotResponse = await snapshotResponsePromise;
    expect(snapshotResponse.status()).toBe(201);
    const snapshot = unwrap(await snapshotResponse.json());
    const workspaceId = requireServerId(snapshot?.workspace_id, 'workspace id');
    expect(snapshot).toMatchObject({ state: 'active', file_count: 2, replayed: false });
    await expect(page.getByText(/Dateien wurden als Workspace registriert/)).toBeVisible();
    await expect(page.getByTestId('workspace-catalog')).toHaveValue(
      workspaceId,
      { timeout: 60_000 },
    );

    await page.getByTestId('workspace-display-name').fill(projectName);
    const connectionResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST'
        && new URL(response.url()).pathname === '/api/source-control/v1/connections',
      { timeout: 60_000 },
    );
    await page.getByTestId('submit-source').click();
    const connectionResponse = await connectionResponsePromise;
    expect(connectionResponse.ok()).toBeTruthy();
    const connectionCreation = unwrap(await connectionResponse.json());
    const connectionId = requireServerId(
      connectionCreation?.connection?.connection_id,
      'connection id',
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
      { timeout: 60_000 },
    );
    await refresh.click();
    const refreshResponse = await refreshResponsePromise;
    expect(refreshResponse.ok()).toBeTruthy();

    await expect(page.getByTestId('journey-scan')).toBeEnabled({ timeout: 60_000 });
    const scanResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST'
        && new URL(response.url()).pathname === `/api/source-control/v1/connections/${connectionId}/scan`,
      { timeout: 60_000 },
    );
    await page.getByTestId('journey-scan').click();
    const scanResponse = await scanResponsePromise;
    expect(scanResponse.ok()).toBeTruthy();
    const scanResult = unwrap(await scanResponse.json());
    const sourceRevisionId = requireServerId(
      scanResult?.receipt?.source_revision_id
        ?? scanResult?.source_revision_id
        ?? scanResult?.revision?.source_revision_id,
      'source revision id',
    );

    const scanHeaders = await scanResponse.request().allHeaders();
    const authorization = String(scanHeaders.authorization ?? '').trim();
    expect(authorization, 'The browser scan request did not carry Hub authorization.').not.toBe('');
    const sourceControlBase = `${new URL(scanResponse.url()).origin}/api/source-control/v1`;
    const projectQuery = `project_id=${encodeURIComponent(projectId)}`;
    const governanceHeaders = {
      Authorization: authorization,
      'Content-Type': 'application/json',
    };
    const governanceTimeout = 60_000;

    const presetsResponse = await guardedHubRequest('grant preset catalog', () => request.get(
      `${sourceControlBase}/grant-presets?${projectQuery}`,
      { headers: governanceHeaders, timeout: governanceTimeout },
    ));
    expect(presetsResponse.status()).toBe(200);
    const presets = unwrap(await presetsResponse.json())?.items;
    expect(Array.isArray(presets)).toBeTruthy();
    const indexPreset = presets.find((preset: any) =>
      preset?.operation === 'index'
      && preset?.transformation === 'redacted'
      && preset?.purpose === 'knowledge-index',
    );
    const presetId = requireServerId(indexPreset?.preset_id, 'redacted index grant preset id');

    const matrixResponse = await guardedHubRequest('access matrix', () => request.post(
      `${sourceControlBase}/access/matrix?${projectQuery}`,
      {
        headers: governanceHeaders,
        timeout: governanceTimeout,
        data: {
          operation: 'index',
          transformation: 'redacted',
          purpose: 'knowledge-index',
          source_limit: 25,
          destination_limit: 25,
        },
      },
    ));
    expect(matrixResponse.status()).toBe(200);
    const matrixItems = unwrap(await matrixResponse.json())?.items;
    expect(Array.isArray(matrixItems)).toBeTruthy();
    const matrixItem = matrixItems.find(
      (item: any) => item?.source_revision_id === sourceRevisionId,
    );
    expect(matrixItem, 'The Hub access matrix did not contain the scanned revision.').toBeTruthy();
    const destinationId = requireServerId(matrixItem?.destination_id, 'index destination id');

    const policyId = `policy-e2e-${Date.now()}`;
    const policyRuleId = 'allow-redacted-index';
    const draftResponse = await guardedHubRequest('policy draft', () => request.post(
      `${sourceControlBase}/context-policies/${encodeURIComponent(policyId)}/drafts?${projectQuery}`,
      {
        headers: {
          ...governanceHeaders,
          'Idempotency-Key': `policy-draft-${projectId}`,
        },
        timeout: governanceTimeout,
        data: {
          document: {
            schema: 'ananta.context-access-policy.v1',
            policy_id: policyId,
            scope: 'project',
            defaults: {
              send_allowed: false,
              read_allowed: false,
              write_allowed: false,
            },
            rules: [{
              id: policyRuleId,
              description: 'Allow redacted project source indexing on local workers',
              send_allowed: true,
              read_allowed: false,
              write_allowed: false,
              cloud_allowed: false,
              external_worker_allowed: false,
              redaction_required: true,
              summarization_allowed: false,
              approval_required: false,
            }],
            precedence: 100,
          },
          expected_latest_version: null,
          dry_run: false,
        },
      },
    ));
    expect(draftResponse.status()).toBe(201);
    const draft = unwrap(await draftResponse.json());
    expect(draft?.policy_id).toBe(policyId);
    const policyVersion = Number(draft?.version);
    expect(Number.isInteger(policyVersion) && policyVersion > 0).toBeTruthy();
    const draftEtag = requireEtag(draft?.etag, draftResponse.headers()['etag'], 'policy draft');

    const previewResponse = await guardedHubRequest('policy preview', () => request.post(
      `${sourceControlBase}/context-policies/${encodeURIComponent(policyId)}/preview?${projectQuery}`,
      {
        headers: governanceHeaders,
        timeout: governanceTimeout,
        data: {
          version: policyVersion,
          source_revision_id: sourceRevisionId,
          destination_id: destinationId,
          operation: 'index',
          transformation: 'redacted',
        },
      },
    ));
    expect(previewResponse.status()).toBe(200);
    const preview = unwrap(await previewResponse.json());
    expect(preview?.decision).toBe('allow_redacted');
    expect(preview?.matched_rule_path).toContain(policyRuleId);

    const activatePolicyResponse = await guardedHubRequest('policy activation', () => request.post(
      `${sourceControlBase}/context-policies/${encodeURIComponent(policyId)}`
        + `/versions/${policyVersion}/activate?${projectQuery}`,
      {
        headers: {
          ...governanceHeaders,
          'If-Match': draftEtag,
          'Idempotency-Key': `policy-activate-${projectId}`,
        },
        timeout: governanceTimeout,
        data: { dry_run: false },
      },
    ));
    expect(activatePolicyResponse.status()).toBe(200);
    const activatedPolicy = unwrap(await activatePolicyResponse.json());
    expect(activatedPolicy).toMatchObject({
      policy_id: policyId,
      version: policyVersion,
      state: 'active',
    });

    const activePolicyResponse = await guardedHubRequest('active policy read', () => request.get(
      `${sourceControlBase}/context-policies/${encodeURIComponent(policyId)}/active?${projectQuery}`,
      { headers: governanceHeaders, timeout: governanceTimeout },
    ));
    expect(activePolicyResponse.status()).toBe(200);
    const activePolicy = unwrap(await activePolicyResponse.json());
    expect(activePolicy).toMatchObject({ policy_id: policyId, version: policyVersion, state: 'active' });
    const activePolicyEtag = requireEtag(
      activePolicy?.etag,
      activePolicyResponse.headers()['etag'],
      'active policy',
    );

    const grantResponse = await guardedHubRequest('index grant', () => request.post(
      `${sourceControlBase}/grants?${projectQuery}`,
      {
        headers: {
          ...governanceHeaders,
          'If-Match': activePolicyEtag,
          'Idempotency-Key': `grant-${projectId}-${sourceRevisionId}`,
        },
        timeout: governanceTimeout,
        data: {
          source_revision_id: sourceRevisionId,
          destination_id: destinationId,
          policy_id: policyId,
          preset_id: presetId,
          duration_seconds: 1_800,
        },
      },
    ));
    expect(grantResponse.status()).toBe(201);
    const grant = unwrap(await grantResponse.json())?.grant;
    const grantId = requireServerId(grant?.grant_id, 'index grant id');
    expect(grant).toMatchObject({
      source_revision_id: sourceRevisionId,
      destination_id: destinationId,
      operation: 'index',
      transformation: 'redacted',
      purpose: 'knowledge-index',
      state: 'active',
    });

    const connectionReloadPromise = page.waitForResponse(
      (response) => response.request().method() === 'GET'
        && new URL(response.url()).pathname === `/api/source-control/v1/connections/${connectionId}`,
      { timeout: 60_000 },
    );
    await page.getByRole('button', { name: 'Läufe aktualisieren' }).click();
    expect((await connectionReloadPromise).ok()).toBeTruthy();

    const profileSelect = page.locator('#journey-profile');
    const profileId = await firstEnabledOptionValue(profileSelect);
    await profileSelect.selectOption(profileId);
    await expect(page.getByTestId('journey-start-index')).toBeEnabled();
    const runResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST'
        && new URL(response.url()).pathname === `/api/source-control/v1/connections/${connectionId}/runs`,
      { timeout: 60_000 },
    );
    await page.getByTestId('journey-start-index').click();
    const runResponse = await runResponsePromise;
    expect(runResponse.ok()).toBeTruthy();

    const runArticle = await waitForRunArticle(page);
    const indexId = requireServerId(
      (await runArticle.locator('strong').textContent())?.trim(),
      'knowledge index id',
    );
    const activate = runArticle.getByRole('button', { name: 'Aktivieren' });
    await waitForIndexAction(page, activate);
    const activateResponsePromise = page.waitForResponse(
      (response) => response.request().method() === 'POST'
        && new URL(response.url()).pathname === `/api/source-control/v1/indices/${indexId}/activate`,
      { timeout: 60_000 },
    );
    await activate.click();
    const activateResponse = await activateResponsePromise;
    expect(activateResponse.ok()).toBeTruthy();
    await expect(page.getByText('Index wurde serverseitig aktiviert.')).toBeVisible();

    await page.getByRole('link', { name: /Quellenübersicht/ }).click();
    const sourceRow = page.locator('tbody tr').filter({ hasText: connectionId });
    await expect(sourceRow).toBeVisible();
    await expect(sourceRow).toContainText(indexId);
    expect(pageErrors).toEqual([]);

    testInfo.annotations.push(
      { type: 'project-id', description: projectId },
      { type: 'workspace-id', description: workspaceId },
      { type: 'connection-id', description: connectionId },
      { type: 'source-revision-id', description: sourceRevisionId },
      { type: 'destination-id', description: destinationId },
      { type: 'policy-id', description: policyId },
      { type: 'grant-id', description: grantId },
      { type: 'index-id', description: indexId },
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

function requireEtag(bodyValue: unknown, headerValue: unknown, label: string): string {
  const etag = String(bodyValue ?? headerValue ?? '').trim().replace(/^"|"$/g, '');
  expect(etag, `The Hub did not return an ETag for the ${label}.`).not.toBe('');
  return etag;
}

async function guardedHubRequest<T>(label: string, operation: () => Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch {
    throw new Error(`The Hub ${label} request did not complete.`);
  }
}

function createUploadDirectory(testInfo: TestInfo): string {
  const directory = testInfo.outputPath('workspace-folder');
  fs.mkdirSync(path.join(directory, 'src'), { recursive: true });
  fs.writeFileSync(path.join(directory, 'README.md'), '# Browser snapshot\n', 'utf8');
  fs.writeFileSync(
    path.join(directory, 'src', 'main.ts'),
    'export const browserSnapshot = true;\n',
    'utf8',
  );
  return directory;
}

async function firstEnabledOptionValue(select: ReturnType<Page['locator']>): Promise<string> {
  const value = await select.locator('option:not([value=""]):not(:disabled)').first().getAttribute('value');
  return requireServerId(value, 'profile id');
}

async function waitForIndexAction(page: Page, action: ReturnType<Page['locator']>): Promise<void> {
  const refreshRuns = page.getByRole('button', { name: 'Läufe aktualisieren' });
  for (let attempt = 0; attempt < 300; attempt += 1) {
    if (await action.isEnabled().catch(() => false)) return;
    if (await refreshRuns.isEnabled().catch(() => false)) {
      await refreshRuns.click();
    }
    await page.waitForTimeout(2_000);
  }
  await expect(action).toBeEnabled();
}

async function waitForRunArticle(page: Page): Promise<ReturnType<Page['locator']>> {
  const runArticle = page.locator('.run-list article').first();
  const refreshRuns = page.getByRole('button', { name: 'Läufe aktualisieren' });
  for (let attempt = 0; attempt < 300; attempt += 1) {
    if (await runArticle.isVisible().catch(() => false)) return runArticle;
    if (await refreshRuns.isEnabled().catch(() => false)) {
      await refreshRuns.click();
    }
    await page.waitForTimeout(2_000);
  }
  await expect(runArticle).toBeVisible();
  return runArticle;
}

async function clickMainNavigation(page: Page, label: string): Promise<void> {
  const navigation = page.locator('.app-nav:visible');
  const group = navigation.locator('details').filter({ hasText: label }).first();
  if (!(await group.evaluate((element) => (element as HTMLDetailsElement).open))) {
    await group.locator('summary').click();
  }
  await group.getByText(label, { exact: true }).click();
}
