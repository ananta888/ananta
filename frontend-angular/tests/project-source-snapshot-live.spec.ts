import { expect, test, type TestInfo } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { loginFast } from './utils';
import {
  createProjectAndOpenJourney,
  openSourceCard,
  refreshScanAndGrant,
  requireServerId,
  runAndActivate,
  unwrap,
  validateAndCreateJourneyConnection,
} from './project-source-live-journey.helpers';

test.describe.configure({ mode: 'serial', retries: 0 });

test.describe('live project, source and snapshot journey', () => {
  test.skip(process.env.RUN_PROJECT_SOURCE_LIVE_E2E !== '1', 'Runs only against an explicitly selected live Ananta composition.');

  test('drives a browser folder exclusively through the visible project and source journey', async ({ page, request }, testInfo) => {
    test.setTimeout(1_200_000);
    page.setDefaultTimeout(120_000);
    const pageErrors: string[] = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await loginFast(page, request);
    const projectName = `Browser Snapshot ${Date.now()}`;
    const projectId = await createProjectAndOpenJourney(
      page,
      projectName,
      'Live Playwright project for the UI-only workspace snapshot journey.',
    );

    await openSourceCard(page, 'Lokaler Ordner / lokale Git-Arbeitskopie');
    const uploadDirectory = createUploadDirectory(testInfo);
    await page.getByTestId('workspace-snapshot-folder').setInputFiles(uploadDirectory);
    await expect(page.getByText(/2 Dateien,/)).toBeVisible();
    await page.getByTestId('workspace-snapshot-name').fill(projectName);
    const snapshotPromise = page.waitForResponse(response =>
      response.request().method() === 'POST'
        && new URL(response.url()).pathname === '/api/source-control/v1/workspace-snapshots',
    );
    await page.getByTestId('upload-workspace-snapshot').click();
    const snapshotResponse = await snapshotPromise;
    expect(snapshotResponse.status()).toBe(201);
    const workspaceId = requireServerId(unwrap(await snapshotResponse.json())?.workspace_id, 'workspace id');
    await expect(page.locator('#journey-workspace')).toHaveValue(workspaceId, { timeout: 60_000 });

    const connectionId = await validateAndCreateJourneyConnection(page, projectName);
    const access = await refreshScanAndGrant(page, connectionId);
    const indexId = await runAndActivate(page, connectionId);
    expect(pageErrors).toEqual([]);
    testInfo.annotations.push(
      { type: 'project-id', description: projectId },
      { type: 'workspace-id', description: workspaceId },
      { type: 'connection-id', description: connectionId },
      { type: 'source-revision-id', description: access.sourceRevisionId },
      { type: 'destination-id', description: access.destinationId },
      { type: 'grant-id', description: access.grantId },
      { type: 'index-id', description: indexId },
    );
  });
});

function createUploadDirectory(testInfo: TestInfo): string {
  const directory = testInfo.outputPath('workspace-folder');
  fs.mkdirSync(path.join(directory, 'src'), { recursive: true });
  fs.writeFileSync(path.join(directory, 'README.md'), '# Browser snapshot\n', 'utf8');
  fs.writeFileSync(path.join(directory, 'src', 'main.ts'), 'export const browserSnapshot = true;\n', 'utf8');
  return directory;
}
