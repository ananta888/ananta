import { expect, Page, test } from '@playwright/test';

import {
  SOURCE_CONTROL_TEST_SUPPORT_BASE,
  SOURCE_CONTROL_TEST_SUPPORT_CONTRACT,
  installSourceControlTestSupport,
} from './helpers/source-control-test-support';

interface SupportResponse {
  status: number;
  body: any;
}

async function supportRequest(
  page: Page,
  path: string,
  options: { method?: 'GET' | 'POST'; body?: Record<string, unknown> } = {},
): Promise<SupportResponse> {
  const result = await page.evaluate(async ({ url, method, body }) => {
    const response = await fetch(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    return { status: response.status, body: await response.json() };
  }, {
    url: `${SOURCE_CONTROL_TEST_SUPPORT_BASE}${path}`,
    method: options.method || 'GET',
    body: options.body,
  });
  expect(result.body.test_support).toEqual({
    contract: SOURCE_CONTROL_TEST_SUPPORT_CONTRACT,
    deterministic: true,
    production_capability: false,
  });
  return result;
}

async function bootstrap(page: Page): Promise<void> {
  await installSourceControlTestSupport(page);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  const capabilities = await supportRequest(page, '/capabilities');
  expect(capabilities.status).toBe(200);
  expect(capabilities.body.data.capabilities).toEqual({
    workspace_vertical: true,
    github_vertical: true,
    governance_preview: true,
    approval_single_use: true,
    index_rollback: true,
  });
}

async function scanWorkspace(page: Page) {
  const inventory = await supportRequest(page, '/workspaces');
  const selected = inventory.body.data.items.find((item: any) => item.label === 'Registered main workspace');
  expect(selected).toBeTruthy();
  const scan = await supportRequest(page, '/workspace/scan', {
    method: 'POST',
    body: { workspace_id: selected.workspace_id, relative_path: 'src' },
  });
  expect(scan.status).toBe(200);
  return scan.body.data;
}

async function createReadyIndex(page: Page, sourceRevisionId: string) {
  const created = await supportRequest(page, '/indexes', {
    method: 'POST',
    body: { source_revision_id: sourceRevisionId },
  });
  const indexId = created.body.data.index_id;
  expect(indexId).toBeTruthy();
  let current = created.body.data;
  for (let attempt = 0; attempt < 3 && current.status !== 'ready'; attempt += 1) {
    current = (await supportRequest(page, `/indexes/${encodeURIComponent(indexId)}`)).body.data;
  }
  expect(current.status).toBe('ready');
  expect(current.progress_percent).toBe(100);
  return current;
}

test.describe.configure({ mode: 'serial', retries: 0 });

test.describe('Source Control vertical deterministic test-support contract', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    testInfo.annotations.push({
      type: 'test-support',
      description: 'Deterministic route fixture; does not assert production backend capability.',
    });
    await bootstrap(page);
  });

  test('workspace selection -> scan -> revision -> index progress -> graph -> local grant', async ({ page }) => {
    const scanned = await scanWorkspace(page);
    const connectionId = scanned.connection.connection_id;
    const revisionId = scanned.revision.source_revision_id;
    expect(connectionId).toBeTruthy();
    expect(revisionId).toBeTruthy();
    expect(scanned.scan).toMatchObject({ status: 'completed', relative_path: 'src' });

    const index = await createReadyIndex(page, revisionId);
    expect(index.source_revision_id).toBe(revisionId);
    const graph = await supportRequest(page, `/indexes/${encodeURIComponent(index.index_id)}/graph`);
    expect(graph.body.data.index_id).toBe(index.index_id);
    expect(graph.body.data.source_revision_id).toBe(revisionId);
    expect(graph.body.data.nodes).toHaveLength(2);
    expect(graph.body.data.edges).toHaveLength(1);

    const destinations = await supportRequest(page, '/destinations');
    const local = destinations.body.data.items.find((item: any) =>
      item.provider_location === 'local_container');
    expect(local).toBeTruthy();
    const grant = await supportRequest(page, '/grants', {
      method: 'POST',
      body: {
        source_revision_id: revisionId,
        destination_id: local.destination_id,
        model_id: local.model_id,
      },
    });
    expect(grant.status).toBe(201);
    expect(grant.body.data.grant.source_revision_id).toBe(revisionId);
    expect(grant.body.data.grant.destination_id).toBe(local.destination_id);
    expect(grant.body.data.decision).toMatchObject({
      decision: 'allow',
      reason_code: 'local_destination_allowed',
    });
  });

  test('GitHub installation -> repository -> commit -> refresh -> stale -> reindex without network', async ({ page }) => {
    const githubNetwork: string[] = [];
    await page.route('https://api.github.com/**', async (route) => {
      githubNetwork.push(route.request().url());
      await route.abort();
    });
    await page.route('https://github.com/**', async (route) => {
      githubNetwork.push(route.request().url());
      await route.abort();
    });

    const installations = await supportRequest(page, '/github/installations');
    const installation = installations.body.data.items[0];
    const repositories = await supportRequest(
      page,
      `/github/installations/${encodeURIComponent(installation.installation_id)}/repositories`,
    );
    const repository = repositories.body.data.items[0];
    const scanned = await supportRequest(page, '/github/scan', {
      method: 'POST',
      body: {
        installation_id: installation.installation_id,
        repository_id: repository.repository_id,
        commit: repository.head_commit,
      },
    });
    const connectionId = scanned.body.data.connection.connection_id;
    const firstRevisionId = scanned.body.data.revision.source_revision_id;
    const firstIndex = await createReadyIndex(page, firstRevisionId);

    const refreshed = await supportRequest(
      page,
      `/connections/${encodeURIComponent(connectionId)}/refresh`,
      { method: 'POST' },
    );
    const refreshedRevisionId = refreshed.body.data.revision.source_revision_id;
    expect(refreshed.body.data.revision.revision_token).toBe(repository.next_commit);
    expect(refreshedRevisionId).not.toBe(firstRevisionId);

    const stale = await supportRequest(page, `/indexes/${encodeURIComponent(firstIndex.index_id)}`);
    expect(stale.body.data.status).toBe('stale');
    const reindexed = await supportRequest(
      page,
      `/indexes/${encodeURIComponent(firstIndex.index_id)}/reindex`,
      { method: 'POST' },
    );
    expect(reindexed.body.data.index.source_revision_id).toBe(refreshedRevisionId);
    expect(reindexed.body.data.index.status).toBe('ready');
    expect(reindexed.body.data.active_index_id).toBe(reindexed.body.data.index.index_id);
    expect(githubNetwork).toEqual([]);
  });

  test('governance preview allows the returned local destination and runtime-denies concrete Claude', async ({ page }) => {
    const scanned = await scanWorkspace(page);
    const revisionId = scanned.revision.source_revision_id;
    const destinations = (await supportRequest(page, '/destinations')).body.data.items;
    const local = destinations.find((item: any) => item.provider_location === 'local_container');
    const claude = destinations.find((item: any) => item.provider_location === 'external_region');
    expect(claude.model_id).toBe('claude-3-7-sonnet-20250219');
    expect(claude.runtime_kind).toBe('cloud_worker');

    const localPreview = await supportRequest(page, '/governance/preview', {
      method: 'POST',
      body: {
        source_revision_id: revisionId,
        destination_id: local.destination_id,
        model_id: local.model_id,
      },
    });
    expect(localPreview.body.data).toMatchObject({
      source_revision_id: revisionId,
      destination_id: local.destination_id,
      model_id: local.model_id,
      decision: 'allow',
      reason_code: 'local_destination_allowed',
    });

    const claudePreview = await supportRequest(page, '/governance/preview', {
      method: 'POST',
      body: {
        source_revision_id: revisionId,
        destination_id: claude.destination_id,
        model_id: claude.model_id,
      },
    });
    expect(claudePreview.body.data).toMatchObject({
      source_revision_id: revisionId,
      destination_id: claude.destination_id,
      model_id: claude.model_id,
      runtime_kind: claude.runtime_kind,
      decision: 'deny',
      reason_code: 'runtime_not_allowed',
    });
  });

  test('single-use approval is bound to returned model, destination and revision', async ({ page }) => {
    const scanned = await scanWorkspace(page);
    const revisionId = scanned.revision.source_revision_id;
    const destinations = (await supportRequest(page, '/destinations')).body.data.items;
    const presets = (await supportRequest(page, '/governance/presets')).body.data.items;
    const approvalPreset = presets.find((item: any) => item.approval_required === true);
    const approvalDestination = destinations.find((item: any) =>
      item.destination_id === approvalPreset.destination_id);
    const otherDestination = destinations.find((item: any) =>
      item.destination_id !== approvalDestination.destination_id);

    const preview = await supportRequest(page, '/governance/preview', {
      method: 'POST',
      body: {
        source_revision_id: revisionId,
        destination_id: approvalDestination.destination_id,
        model_id: approvalDestination.model_id,
        preset_ref: approvalPreset.preset_ref,
      },
    });
    expect(preview.body.data).toMatchObject({
      decision: 'approval_required',
      reason_code: 'approval_required',
    });
    const approved = await supportRequest(page, '/approvals', {
      method: 'POST',
      body: {
        source_revision_id: revisionId,
        destination_id: approvalDestination.destination_id,
        model_id: approvalDestination.model_id,
        preset_ref: approvalPreset.preset_ref,
      },
    });
    const approvalId = approved.body.data.approval_id;

    const mismatched = await supportRequest(page, '/grants', {
      method: 'POST',
      body: {
        source_revision_id: revisionId,
        destination_id: approvalDestination.destination_id,
        model_id: otherDestination.model_id,
        approval_id: approvalId,
      },
    });
    expect(mismatched.status).toBe(403);
    expect(mismatched.body.reason_code).toBe('approval_binding_mismatch');

    const firstUse = await supportRequest(page, '/grants', {
      method: 'POST',
      body: {
        source_revision_id: revisionId,
        destination_id: approvalDestination.destination_id,
        model_id: approvalDestination.model_id,
        approval_id: approvalId,
      },
    });
    expect(firstUse.status).toBe(201);
    expect(firstUse.body.data.grant).toMatchObject({
      source_revision_id: revisionId,
      destination_id: approvalDestination.destination_id,
    });

    const secondUse = await supportRequest(page, '/grants', {
      method: 'POST',
      body: {
        source_revision_id: revisionId,
        destination_id: approvalDestination.destination_id,
        model_id: approvalDestination.model_id,
        approval_id: approvalId,
      },
    });
    expect(secondUse.status).toBe(409);
    expect(secondUse.body.reason_code).toBe('approval_consumed');
  });

  test('index rollback moves the active pointer and binds audit plus retrieval to the target revision', async ({ page }) => {
    const scanned = await scanWorkspace(page);
    const connectionId = scanned.connection.connection_id;
    const firstRevisionId = scanned.revision.source_revision_id;
    const firstIndex = await createReadyIndex(page, firstRevisionId);

    const refreshed = await supportRequest(
      page,
      `/connections/${encodeURIComponent(connectionId)}/refresh`,
      { method: 'POST' },
    );
    const secondRevisionId = refreshed.body.data.revision.source_revision_id;
    const replacement = await supportRequest(
      page,
      `/indexes/${encodeURIComponent(firstIndex.index_id)}/reindex`,
      { method: 'POST' },
    );
    const secondIndexId = replacement.body.data.active_index_id;
    expect(replacement.body.data.index.source_revision_id).toBe(secondRevisionId);

    const rollback = await supportRequest(
      page,
      `/connections/${encodeURIComponent(connectionId)}/indexes/rollback`,
      { method: 'POST', body: { target_index_id: firstIndex.index_id } },
    );
    expect(rollback.body.data).toMatchObject({
      previous_active_index_id: secondIndexId,
      active_index_id: firstIndex.index_id,
    });

    const state = await supportRequest(
      page,
      `/connections/${encodeURIComponent(connectionId)}/index-state`,
    );
    expect(state.body.data.active_index_id).toBe(firstIndex.index_id);

    const audit = await supportRequest(
      page,
      `/audit?connection_id=${encodeURIComponent(connectionId)}`,
    );
    const event = audit.body.data.items.find((item: any) =>
      item.audit_id === rollback.body.data.audit_id);
    expect(event).toMatchObject({
      event_type: 'source_index_rollback',
      connection_id: connectionId,
      previous_active_index_id: secondIndexId,
      active_index_id: firstIndex.index_id,
      source_revision_id: firstRevisionId,
    });

    const retrieval = await supportRequest(page, '/retrieve', {
      method: 'POST',
      body: { connection_id: connectionId, query: 'entry' },
    });
    expect(retrieval.body.data).toMatchObject({
      connection_id: connectionId,
      active_index_id: firstIndex.index_id,
      source_revision_id: firstRevisionId,
    });
    expect(retrieval.body.data.chunks[0].source_revision_id).toBe(firstRevisionId);
  });
});
