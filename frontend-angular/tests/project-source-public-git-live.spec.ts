import { expect, test } from '@playwright/test';

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

test.describe('live public Git source journey', () => {
  test.skip(process.env.RUN_PROJECT_SOURCE_LIVE_E2E !== '1', 'Runs only against an explicitly selected live Ananta composition.');

  test('drives public Git through visible project, access, run and activation controls', async ({ page, request }, testInfo) => {
    test.setTimeout(1_200_000);
    page.setDefaultTimeout(120_000);
    const pageErrors: string[] = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await loginFast(page, request);
    const projectName = `Public Git ${Date.now()}`;
    const projectId = await createProjectAndOpenJourney(
      page,
      projectName,
      'Live Playwright project for the UI-only public Git journey.',
    );

    await openSourceCard(page, 'Öffentliches Git/GitHub-Repository');
    await page.getByTestId('public-git-url').fill('https://github.com/octocat/Hello-World');
    await page.getByTestId('public-git-ref').fill('master');
    const validationPromise = page.waitForResponse(response =>
      response.request().method() === 'POST'
        && new URL(response.url()).pathname === '/api/source-control/v1/public-remotes/validate',
    );
    const registrationPromise = page.waitForResponse(response =>
      response.request().method() === 'POST'
        && new URL(response.url()).pathname === '/api/source-control/v1/public-remotes',
    );
    await page.getByTestId('create-public-remote').click();
    expect((await validationPromise).ok()).toBeTruthy();
    const registration = await registrationPromise;
    expect(registration.status()).toBe(201);
    const remote = unwrap(await registration.json());
    const remoteId = requireServerId(remote?.remote_id, 'public remote id');
    const commitSha = requireServerId(remote?.commit_sha, 'resolved commit');
    await expect(page.locator('#journey-remote')).toHaveValue(remoteId, { timeout: 60_000 });
    await expect(page.locator('app-notifications .notification.error')).toHaveCount(0);

    const connectionId = await validateAndCreateJourneyConnection(page, projectName);
    const access = await refreshScanAndGrant(page, connectionId);
    const indexId = await runAndActivate(page, connectionId);
    expect(pageErrors).toEqual([]);
    testInfo.annotations.push(
      { type: 'project-id', description: projectId },
      { type: 'public-remote-id', description: remoteId },
      { type: 'public-remote-commit', description: commitSha },
      { type: 'connection-id', description: connectionId },
      { type: 'source-revision-id', description: access.sourceRevisionId },
      { type: 'destination-id', description: access.destinationId },
      { type: 'grant-id', description: access.grantId },
      { type: 'index-id', description: indexId },
    );
  });
});
