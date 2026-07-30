import { createServer } from 'node:http';
import type { IncomingHttpHeaders, IncomingMessage, ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { Page } from '@playwright/test';

export interface LocalHubOperation {
  readonly method: string;
  readonly path: string;
  readonly body: unknown;
  readonly headers: IncomingHttpHeaders;
}

export interface SourceControlLocalHub {
  readonly origin: string;
  readonly operations: LocalHubOperation[];
  install(page: Page): Promise<void>;
  close(): Promise<void>;
}

const CONNECTION_ID = `conn_${'1'.repeat(64)}`;
const REVISION_ID = `srev_${'2'.repeat(64)}`;
const INDEX_ID = `idx_${'3'.repeat(64)}`;
const GRANT_ID = `grant_${'4'.repeat(64)}`;
const GRANT_FAMILY_ID = `grantfam_${'5'.repeat(64)}`;
const CONTENT_ID = `content_${'6'.repeat(64)}`;
const NOW = '2026-07-30T10:00:00Z';

function responseEnvelope(data: unknown): unknown {
  return {
    schema: 'ananta.source-control.api-response.v1',
    data,
  };
}

function json(
  response: ServerResponse,
  status: number,
  data: unknown,
  headers: Record<string, string> = {},
): void {
  const body = JSON.stringify(responseEnvelope(data));
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    ...headers,
  });
  response.end(body);
}

async function requestBody(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  if (chunks.length === 0) return null;
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function connection(body: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: 'ananta.source-control.source-connection.v1',
    authority: 'hub',
    connection_id: CONNECTION_ID,
    tenant_id: 'tenant-local-e2e',
    project_id: 'project-alpha',
    owner_id: 'user-local-e2e',
    connector_type: body['connector_type'] ?? 'direct_text',
    connection_identity_digest: 'a'.repeat(64),
    display_name: body['display_name'] ?? 'Local deterministic source',
    sensitivity: body['sensitivity'] ?? 'internal',
    state: 'ready',
    created_at: NOW,
  };
}

function runRecord(state = 'ready'): Record<string, unknown> {
  return {
    knowledge_index_id: INDEX_ID,
    connection_id: CONNECTION_ID,
    source_revision_id: REVISION_ID,
    index_profile_id: 'profile-default',
    status: state,
    etag: '8'.repeat(64),
    coverage: { percent: 100 },
    created_at: NOW,
    updated_at: NOW,
  };
}

function grantRecord(state = 'active'): Record<string, unknown> {
  return {
    schema: 'ananta.source-control.grant-admin-item.v1',
    grant_id: GRANT_ID,
    grant_family_id: GRANT_FAMILY_ID,
    version: state === 'active' ? 1 : 2,
    source_revision_id: REVISION_ID,
    destination_id: 'hub-destination-primary',
    preset_id: 'preset-read',
    operation: 'read',
    transformation: 'none',
    purpose: 'analysis',
    policy_version: 'policy-primary:7',
    state,
    issued_at: NOW,
    expires_at: '2026-07-30T10:30:00Z',
    expired: false,
    etag: (state === 'active' ? '7' : '6').repeat(64),
  };
}

function projection(): Record<string, unknown> {
  const projectionConnection = connection();
  delete projectionConnection['tenant_id'];
  return {
    schema: 'ananta.source-control.projection.v1',
    connection_id: CONNECTION_ID,
    etag: '9'.repeat(64),
    connection: projectionConnection,
    revision: {
      source_revision_id: REVISION_ID,
      revision_digest: 'b'.repeat(64),
      sensitivity: 'internal',
    },
    admission: { state: 'admitted' },
    index: {
      knowledge_index_id: INDEX_ID,
      source_revision_id: REVISION_ID,
      status: 'ready',
      policy_digest: 'd'.repeat(64),
    },
    active_index: null,
    grants: [],
    health: {},
    next_actions: ['index', 'activate', 'rollback', 'grant', 'refresh', 'scan', 'disable'],
    stale: false,
  };
}

function policyVersion(
  version: number,
  state: 'draft' | 'active' | 'superseded' | 'revoked',
): Record<string, unknown> {
  return {
    policy_id: 'policy-primary',
    version,
    tenant_id: 'tenant-local-e2e',
    project_id: 'project-alpha',
    state,
    document: {
      schema: 'ananta.context-access-policy.v1',
      policy_id: 'policy-primary',
      scope: 'project',
      defaults: { decision: 'deny' },
      rules: [],
      precedence: [],
    },
    policy_digest: (version === 1 ? 'd' : version === 2 ? 'c' : 'b').repeat(64),
    etag: (version === 1 ? 'f' : version === 2 ? 'e' : 'a').repeat(64),
    created_by: 'user-local-e2e',
    created_at: NOW,
  };
}

async function handle(
  request: IncomingMessage,
  response: ServerResponse,
  operations: LocalHubOperation[],
  state: {
    grantState: string;
    indexState: string;
    policyLatestVersion: number;
    policyActiveVersion: number;
  },
): Promise<void> {
  const method = request.method ?? 'GET';
  const requestUrl = new URL(request.url ?? '/', 'http://local-hub.invalid');
  const body = await requestBody(request);
  operations.push({ method, path: requestUrl.pathname, body, headers: request.headers });
  const recordBody =
    body && typeof body === 'object' && !Array.isArray(body)
      ? (body as Record<string, unknown>)
      : {};

  if (requestUrl.pathname === '/api/source-control/v1/__test-support') {
    json(response, 200, {
      contract: 'local-deterministic-hub',
      deterministic: true,
      production_capability: false,
    });
    return;
  }

  if (method === 'GET' && requestUrl.pathname === '/api/source-control/v1/workspaces') {
    json(response, 200, {
      items: [
        {
          workspace_id: 'workspace-primary',
          enabled: true,
          read_only: true,
          capabilities: { connect: true },
        },
      ],
      next_cursor: null,
      capabilities: { connect: true },
    });
    return;
  }

  if (
    method === 'GET' &&
    requestUrl.pathname === '/api/source-control/v1/registered-remotes'
  ) {
    json(response, 200, {
      items: [
        {
          remote_id: 'remote-primary',
          kind: 'git',
          repository: 'local/repository',
          state: 'ready',
          capabilities: { connect: true },
        },
      ],
      next_cursor: null,
      capabilities: { connect: true },
    });
    return;
  }

  if (method === 'GET' && requestUrl.pathname === '/api/source-control/v1/index-profiles') {
    json(response, 200, {
      items: [
        {
          profile_id: 'profile-default',
          label: 'Deterministic',
          description: 'Local deterministic index profile',
          is_default: true,
          capabilities: { start: true },
        },
      ],
      next_cursor: null,
      capabilities: { start: true },
    });
    return;
  }

  if (
    method === 'POST' &&
    requestUrl.pathname === '/api/source-control/v1/content-admissions/validate'
  ) {
    json(response, 200, {
      valid: true,
      preview: {
        source_type: recordBody['source_type'],
        display_name: recordBody['display_name'],
        sensitivity: recordBody['sensitivity'],
        content_digest: 'c'.repeat(64),
      },
    });
    return;
  }

  if (
    method === 'POST' &&
    requestUrl.pathname === '/api/source-control/v1/content-admissions'
  ) {
    json(response, 201, {
      connection: connection(recordBody),
      revision: {
        schema: 'ananta.source-control.source-revision.v1',
        source_revision_id: REVISION_ID,
        connection_id: CONNECTION_ID,
        revision_digest: 'b'.repeat(64),
        captured_at: NOW,
      },
      content: {
        schema: 'ananta.source-control.admitted-content.v1',
        content_id: CONTENT_ID,
        source_revision_id: REVISION_ID,
        content_digest: 'c'.repeat(64),
        media_type: recordBody['media_type'] ?? 'application/vnd.ananta.notebook+json',
      },
    });
    return;
  }

  if (
    method === 'POST' &&
    requestUrl.pathname === '/api/source-control/v1/connections/validate'
  ) {
    json(response, 200, { valid: true, connection: connection(recordBody) });
    return;
  }

  if (method === 'POST' && requestUrl.pathname === '/api/source-control/v1/connections') {
    json(response, 201, { connection: connection(recordBody), version: 1 });
    return;
  }

  if (
    method === 'GET' &&
    requestUrl.pathname === `/api/source-control/v1/connections/${CONNECTION_ID}`
  ) {
    json(response, 200, projection(), { etag: '9'.repeat(64) });
    return;
  }

  if (
    method === 'GET' &&
    requestUrl.pathname === `/api/source-control/v1/connections/${CONNECTION_ID}/runs`
  ) {
    json(response, 200, {
      items: [runRecord(state.indexState)],
      active: state.indexState === 'active' ? runRecord('active') : null,
      next_cursor: null,
    });
    return;
  }

  if (
    method === 'POST' &&
    requestUrl.pathname === `/api/source-control/v1/connections/${CONNECTION_ID}/runs`
  ) {
    state.indexState = 'ready';
    json(response, 202, {
      operation: 'run',
      connection_id: CONNECTION_ID,
      receipt: {
        knowledge_index_id: INDEX_ID,
        status: 'accepted',
      },
    });
    return;
  }

  if (
    method === 'POST' &&
    requestUrl.pathname === `/api/source-control/v1/indices/${INDEX_ID}/activate`
  ) {
    state.indexState = 'active';
    json(response, 200, {
      operation: 'activate',
      resource_id: INDEX_ID,
      result: {
        knowledge_index_id: INDEX_ID,
        generation: 1,
        updated_at: NOW,
      },
    });
    return;
  }

  if (
    method === 'POST' &&
    requestUrl.pathname === `/api/source-control/v1/indices/${INDEX_ID}/rollback`
  ) {
    state.indexState = 'rolled_back';
    json(response, 200, {
      operation: 'rollback',
      resource_id: INDEX_ID,
      result: {
        knowledge_index_id: INDEX_ID,
        generation: 2,
        updated_at: NOW,
      },
    });
    return;
  }

  if (method === 'GET' && requestUrl.pathname === '/api/source-control/v1/grant-presets') {
    json(response, 200, {
      items: [
        {
          schema: 'ananta.source-control.grant-preset.v1',
          preset_id: 'preset-read',
          label: 'Read',
          description: 'Read-only analysis',
          operation: 'read',
          transformation: 'none',
          purpose: 'analysis',
          max_duration_seconds: 1800,
        },
      ],
      next_cursor: null,
      capabilities: { issue: true },
    });
    return;
  }

  if (method === 'GET' && requestUrl.pathname === '/api/source-control/v1/grants') {
    json(response, 200, {
      schema: 'ananta.source-control.grant-admin-list.v1',
      items: [grantRecord(state.grantState)],
      next_cursor: null,
      capabilities: { issue: true, revoke: true },
    });
    return;
  }

  if (method === 'POST' && requestUrl.pathname === '/api/source-control/v1/grants') {
    state.grantState = 'active';
    json(
      response,
      201,
      {
        grant: grantRecord('active'),
        capabilities: { revoke: true },
      },
      { etag: '7'.repeat(64) },
    );
    return;
  }

  if (
    method === 'POST' &&
    requestUrl.pathname === `/api/source-control/v1/grants/${GRANT_ID}/actions/revoke`
  ) {
    state.grantState = 'revoked';
    json(
      response,
      200,
      {
        grant: grantRecord('revoked'),
        capabilities: { revoke: false },
      },
      { etag: '6'.repeat(64) },
    );
    return;
  }

  if (method === 'GET' && requestUrl.pathname === '/api/source-control/v1/events') {
    json(response, 200, {
      events: [],
      next_cursor: null,
      last_sequence: 0,
    });
    return;
  }

  if (
    method === 'GET' &&
    requestUrl.pathname === '/api/source-control/v1/context-policies'
  ) {
    const latestState =
      state.policyLatestVersion === state.policyActiveVersion ? 'active' : 'draft';
    const latest = policyVersion(state.policyLatestVersion, latestState);
    json(response, 200, {
      items: [
        {
          policy_id: latest['policy_id'],
          latest_version: latest['version'],
          state: latest['state'],
          etag: latest['etag'],
          policy_digest: latest['policy_digest'],
        },
      ],
      next_cursor: null,
    });
    return;
  }

  const versionsMatch = requestUrl.pathname.match(
    /^\/api\/source-control\/v1\/context-policies\/policy-primary\/versions$/,
  );
  if (method === 'GET' && versionsMatch) {
    const versions = [
      policyVersion(
        state.policyLatestVersion,
        state.policyLatestVersion === state.policyActiveVersion ? 'active' : 'draft',
      ),
      ...(state.policyLatestVersion > 2 ? [policyVersion(2, 'superseded')] : []),
      policyVersion(
        1,
        state.policyActiveVersion === 1 ? 'active' : 'superseded',
      ),
    ];
    json(response, 200, { items: versions, next_cursor: null });
    return;
  }

  const detailMatch = requestUrl.pathname.match(
    /^\/api\/source-control\/v1\/context-policies\/policy-primary\/versions\/(\d+)$/,
  );
  if (method === 'GET' && detailMatch) {
    const version = Number(detailMatch[1]);
    const versionState =
      version === state.policyActiveVersion
        ? 'active'
        : version === state.policyLatestVersion
          ? 'draft'
          : 'superseded';
    const item = policyVersion(version, versionState);
    json(response, 200, item, { etag: String(item['etag']) });
    return;
  }

  if (
    method === 'GET' &&
    requestUrl.pathname ===
      '/api/source-control/v1/context-policies/policy-primary/active'
  ) {
    const item = policyVersion(state.policyActiveVersion, 'active');
    json(response, 200, item, { etag: String(item['etag']) });
    return;
  }

  const activateMatch = requestUrl.pathname.match(
    /^\/api\/source-control\/v1\/context-policies\/policy-primary\/versions\/(\d+)\/activate$/,
  );
  if (method === 'POST' && activateMatch) {
    const version = Number(activateMatch[1]);
    state.policyActiveVersion = version;
    const item = policyVersion(version, 'active');
    json(response, 200, item, { etag: String(item['etag']) });
    return;
  }

  if (
    method === 'POST' &&
    requestUrl.pathname ===
      '/api/source-control/v1/context-policies/policy-primary/rollback'
  ) {
    const targetVersion = Number(recordBody['target_version']);
    state.policyLatestVersion += 1;
    const item = {
      ...policyVersion(state.policyLatestVersion, 'draft'),
      document: policyVersion(targetVersion, 'superseded')['document'],
    };
    json(response, 201, item, { etag: String(item['etag']) });
    return;
  }

  json(response, 404, {
    code: 'local_hub_route_not_found',
    message: `${method} ${requestUrl.pathname}`,
  });
}

export async function startSourceControlLocalHub(): Promise<SourceControlLocalHub> {
  const operations: LocalHubOperation[] = [];
  const state = {
    grantState: 'active',
    indexState: 'ready',
    policyLatestVersion: 2,
    policyActiveVersion: 1,
  };
  const server = createServer((request, response) => {
    void handle(request, response, operations, state).catch((error: unknown) => {
      json(response, 500, {
        code: 'local_hub_failure',
        message: error instanceof Error ? error.message : 'unknown local hub failure',
      });
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve());
  });
  const address = server.address() as AddressInfo;
  const origin = `http://127.0.0.1:${address.port}`;

  return {
    origin,
    operations,
    async install(page: Page): Promise<void> {
      await page.route('**/api/source-control/v1/**', async (route) => {
        const incoming = route.request();
        const target = new URL(incoming.url());
        const forwarded = await fetch(`${origin}${target.pathname}${target.search}`, {
          method: incoming.method(),
          headers: {
            'content-type': incoming.headers()['content-type'] ?? 'application/json',
            'if-match': incoming.headers()['if-match'] ?? '',
            'idempotency-key': incoming.headers()['idempotency-key'] ?? '',
          },
          body:
            incoming.method() === 'GET' || incoming.method() === 'HEAD'
              ? undefined
              : incoming.postData() ?? undefined,
        });
        await route.fulfill({
          status: forwarded.status,
          headers: Object.fromEntries(forwarded.headers.entries()),
          body: await forwarded.text(),
        });
      });
    },
    close(): Promise<void> {
      return new Promise((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    },
  };
}

export const LOCAL_SOURCE_CONTROL_IDS = {
  connectionId: CONNECTION_ID,
  revisionId: REVISION_ID,
  indexId: INDEX_ID,
  grantId: GRANT_ID,
} as const;
