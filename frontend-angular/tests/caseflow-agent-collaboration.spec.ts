import { expect, test, type Route } from '@playwright/test';

const HUB_ORIGIN = 'http://127.0.0.1:5000';
const GRAPH_ID = 'caseflow-gate-graph';
const RUN_ID = 'caseflow-gate-run';
const AUTHORIZED_USER_TOKEN = [
  'eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0',
  'eyJzdWIiOiJjYXNlZmxvdy1nYXRlLXVzZXIiLCJ1c2VybmFtZSI6ImNhc2VmbG93LWdhdGUtdXNlciIsInJvbGUiOiJhZG1pbiIsImV4cCI6NDEwMjQ0NDgwMH0',
  'gate',
].join('.');

const graph = Object.freeze({
  id: GRAPH_ID,
  name: 'CaseFlow Gate Graph',
  description: 'Deterministic CaseFlow collaboration fixture',
  version: '1',
  definition_revision: 1,
  base_graph_hash: 'a'.repeat(64),
  tags: [],
  steps: [
    {
      id: 'builder',
      label: 'Builder',
      kind: 'task',
      role: 'builder',
      gate: false,
      policy_hints: [],
      position: { x: 0, y: 0 },
      io: { inputs: [], outputs: [] },
    },
    {
      id: 'critic',
      label: 'Critic',
      kind: 'review',
      role: 'critic',
      gate: false,
      policy_hints: [],
      position: { x: 260, y: 0 },
      io: { inputs: [], outputs: [] },
    },
  ],
  edges: [
    {
      id: 'builder-critic',
      source: 'builder',
      target: 'critic',
      label: 'Review',
      condition: { kind: 'always' },
    },
  ],
});

const graphSummary = Object.freeze({
  id: GRAPH_ID,
  name: graph.name,
  description: graph.description,
  tags: [],
  created_at: 1,
  updated_at: 1,
});

const runtimeStatus = Object.freeze({
  schema: 'ananta.workflow_backend_status.v1',
  backend: 'hub',
  workflow_id: GRAPH_ID,
  run_id: RUN_ID,
  process_id: GRAPH_ID,
  snapshot_hash: graph.base_graph_hash,
  revision: 7,
  status: 'running',
  updated_at: 7,
  steps: [
    { step_id: 'builder', status: 'running', started_at: 5 },
    { step_id: 'critic', status: 'pending' },
  ],
});

const traceReadModel = Object.freeze({
  schema: 'ananta.caseflow_edge_trace_read_model.v1',
  workflow_id: GRAPH_ID,
  run_id: RUN_ID,
  catalog_verification_status: 'verified',
  verification_status: 'verified',
  reason_code: '',
  edges: [
    {
      edge_id: 'builder-critic',
      source_step_id: 'builder',
      target_step_id: 'critic',
      edge_kind: 'dependency',
      activity_status: 'active',
      verification_status: 'verified',
      reason_code: 'caseflow_edge_correlation_verified_active',
      correlation_basis: 'explicit_edge_id',
      event_refs: ['event-builder-critic'],
      trace_refs: ['trace-builder-critic'],
      messages: [
        {
          content: 'Builder result forwarded for review',
          role: 'builder',
          event_ref: 'event-builder-critic',
          trace_ref: 'trace-builder-critic',
          correlation_ref: 'trace-builder-critic',
          occurred_at: 6,
          verification_status: 'verified',
          truncated: false,
        },
      ],
      telemetry: [
        {
          event_ref: 'event-builder-critic',
          trace_ref: 'trace-builder-critic',
          agent_run_ref: 'agent-run-builder',
          correlation_ref: 'trace-builder-critic',
          causation_ref: null,
          event_type: 'agent.message',
          step_id: 'builder',
          sequence: 1,
          occurred_at: 6,
          status: 'running',
          duration_ms: 1,
          model: null,
          provider: null,
          token_usage: null,
          cost_micros: null,
          tool: null,
          error: null,
          redaction_policy: 'user',
        },
      ],
      limits: {
        messages_truncated: 0,
        telemetry_truncated: 0,
        event_refs_truncated: 0,
        trace_refs_truncated: 0,
      },
    },
  ],
  telemetry: {
    source_event_count: 1,
    processed_event_count: 1,
    rejected_event_count: 0,
    truncated_event_count: 0,
    correlated_edge_count: 1,
    redaction_policy: 'user',
    messages_per_edge_limit: 64,
    telemetry_per_edge_limit: 128,
  },
});

test('Studio menu keeps one Hub graph draft and its authorized runtime projection across views', async ({
  context,
  page,
}) => {
  const requests: Array<{ method: string; path: string; authorized: boolean }> = [];
  await context.addInitScript(({ hubOrigin, token }) => {
    localStorage.clear();
    localStorage.setItem('ananta.user.token', token);
    localStorage.setItem('ananta.shell.mode', 'simple');
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', role: 'hub', url: hubOrigin, token: '' },
    ]));

    class QuietEventSource {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly CONNECTING = 0;
      readonly OPEN = 1;
      readonly CLOSED = 2;
      readonly readyState = QuietEventSource.OPEN;
      readonly url: string;
      readonly withCredentials = false;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(url: string | URL) { this.url = String(url); }
      addEventListener(): void {}
      removeEventListener(): void {}
      dispatchEvent(): boolean { return true; }
      close(): void {}
    }
    Object.defineProperty(window, 'EventSource', {
      configurable: true,
      value: QuietEventSource,
    });
  }, { hubOrigin: HUB_ORIGIN, token: AUTHORIZED_USER_TOKEN });

  await page.route(`${HUB_ORIGIN}/**`, async route => {
    const request = route.request();
    const url = new URL(request.url());
    const authorized = request.headers()['authorization'] === `Bearer ${AUTHORIZED_USER_TOKEN}`;
    requests.push({ method: request.method(), path: url.pathname, authorized });
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: corsHeaders() });
      return;
    }
    if (requiresUserAuthorization(url.pathname) && !authorized) {
      await fulfillJson(route, { error: 'caseflow_gate_unauthorized' }, 401);
      return;
    }
    await fulfillHubRequest(route, request.method(), url.pathname);
  });

  await page.goto('/help');
  const navigation = page.getByRole('navigation', { name: 'Hauptnavigation' });
  await expect(navigation).toBeVisible();
  const caseFlowGroup = navigation.locator('details').filter({
    has: page.getByText('CaseFlow', { exact: true }),
  });
  await expect(caseFlowGroup).toHaveCount(1);
  await caseFlowGroup.locator('summary').click();
  const studioLink = caseFlowGroup.getByRole('link', { name: 'CaseFlow Studio', exact: true });
  await expect(studioLink).toHaveCount(1);
  await studioLink.click();

  await expect(page.getByRole('heading', { name: 'CaseFlow Studio', exact: true })).toBeVisible();
  const graphSelect = page.getByLabel('Gespeicherter Visual Process');
  await expect(graphSelect).toHaveValue('');
  await graphSelect.selectOption(GRAPH_ID);
  await expect(page.locator('app-caseflow-agent-canvas')).toBeVisible();
  await expect(page.locator('[data-caseflow-runtime-status]')).toContainText('Workflow');
  await expect(page.locator('[data-caseflow-runtime-status]')).toContainText(RUN_ID);
  await expect(page.locator('[data-step-id="builder"]')).toContainText('Läuft');

  await page.locator('[data-step-id="builder"]').click();
  await expect(page.getByText('Runtime-Metriken', { exact: true })).toBeVisible();
  await expect(page.locator('[data-selected-kind="node"]')).toContainText('running');

  const edge = page.locator('app-caseflow-agent-canvas [data-edge-id="builder-critic"]');
  await expect(edge).toContainText('Aktiv');
  await edge.click();
  const edgeInspector = page.locator('[data-selected-kind="edge"]');
  await expect(edgeInspector.getByRole('heading', { name: 'builder → critic' })).toBeVisible();
  await expect(edgeInspector).toContainText('active');
  await expect(edgeInspector).toContainText('verified');
  await edgeInspector.getByRole('tab', { name: 'Telemetrie' }).click();
  await expect(edgeInspector.getByTestId('caseflow-edge-run-scope')).toContainText(RUN_ID);
  await expect(edgeInspector).toContainText('trace-builder-critic');
  await expect(edgeInspector).toContainText('agent.message');

  await page.getByRole('tab', { name: 'Vollständiger Prozess' }).click();
  const titleInput = page.locator('.vpe-title-input');
  await expect(titleInput).toHaveValue(graph.name);
  await titleInput.fill('Ungespeicherter CaseFlow Gate Draft');
  await expect(page.locator('.vpe-dirty')).toBeVisible();

  await page.getByRole('tab', { name: 'Agenten', exact: true }).click();
  await expect(page.locator('app-caseflow-agent-canvas')).toBeVisible();
  await expect(page.locator('[data-caseflow-runtime-status]')).toContainText(RUN_ID);
  await page.getByRole('tab', { name: 'Vollständiger Prozess' }).click();
  await expect(titleInput).toHaveValue('Ungespeicherter CaseFlow Gate Draft');

  const graphLoads = requests.filter(item =>
    item.method === 'GET' && item.path === `/api/visual-process/graphs/${GRAPH_ID}`);
  const runtimeReads = requests.filter(item =>
    item.path === `/api/visual-process/workflow/${GRAPH_ID}/status`
    || item.path === `/api/visual-process/workflow/${GRAPH_ID}/caseflow-edge-trace`);
  const graphWrites = requests.filter(item =>
    (item.method === 'POST' && item.path === '/api/visual-process/graphs')
    || (item.method === 'PUT' && item.path === `/api/visual-process/v2/graphs/${GRAPH_ID}`));
  expect(graphLoads).toHaveLength(1);
  expect([...graphLoads, ...runtimeReads].every(item => item.authorized)).toBe(true);
  expect(runtimeReads).toHaveLength(2);
  expect(graphWrites).toHaveLength(0);
  await expect(graphSelect).toHaveValue(GRAPH_ID);
});

async function fulfillHubRequest(route: Route, method: string, path: string): Promise<void> {
  if (method === 'GET' && path === '/me') {
    return fulfillJson(route, { status: 'success', data: { username: 'caseflow-gate-user', role: 'admin' } });
  }
  if (method === 'GET' && path === '/api/network-profiles/local') {
    return fulfillJson(route, {
      ok: true,
      profile: {
        profile_id: 'local',
        label: 'CaseFlow gate',
        oidc: { issuer: '', client_id: '', audience: 'ananta-hub', pkce_required: true },
        rendezvous: { base_url: '', signaling_url: '', transport_order: ['hub_relay'] },
        ice_servers: [],
        require_e2e_payload_encryption: false,
        signaling_url: '',
        transport_order: ['hub_relay'],
        semantic_media_feature_flags: {},
        warning: '',
      },
    });
  }
  if (method === 'GET' && path === '/config/features/v1') {
    return fulfillJson(route, {
      schema: 'ananta.dashboard-feature-flags.v1',
      features: { angular_kanban: false, angular_model_dashboard: false },
    });
  }
  if (method === 'GET' && path === '/api/visual-process/graphs') {
    return fulfillJson(route, [graphSummary]);
  }
  if (method === 'GET' && path === `/api/visual-process/graphs/${GRAPH_ID}`) {
    return fulfillJson(route, graph);
  }
  if (method === 'GET' && path === `/api/visual-process/workflow/${GRAPH_ID}/status`) {
    return fulfillJson(route, runtimeStatus);
  }
  if (method === 'POST' && path === `/api/visual-process/workflow/${GRAPH_ID}/caseflow-edge-trace`) {
    return fulfillJson(route, traceReadModel);
  }
  if (method === 'GET' && path === '/api/visual-process/node-definitions') {
    return fulfillJson(route, {
      schema: 'ananta.visual_process.node_definition_registry.v1',
      registry_version: 'caseflow-gate',
      definitions: [],
    });
  }
  if (method === 'GET' && path === '/config/model-routing/profiles') {
    return fulfillJson(route, {
      status: 'success',
      data: { profiles: [], fallback_groups: {}, status: 'loaded' },
    });
  }
  if (method === 'GET' && path === '/api/ml-intern-training/capabilities') {
    return fulfillJson(route, { gpu_profiles: [], base_models: [] });
  }
  if (method === 'GET' && path === '/api/ml-intern-training/datasets') {
    return fulfillJson(route, []);
  }
  if (method === 'GET' && [
    '/api/visual-process/presets',
    '/api/visual-process/skill-profiles',
    '/api/visual-process/task-kinds',
    '/instruction-profiles',
    '/sources',
  ].includes(path)) {
    return fulfillJson(route, []);
  }
  return fulfillJson(route, []);
}

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    headers: { ...corsHeaders(), 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function requiresUserAuthorization(path: string): boolean {
  return path === '/api/visual-process/graphs'
    || path.startsWith('/api/visual-process/graphs/')
    || path.startsWith('/api/visual-process/workflow/');
}

function corsHeaders(): Record<string, string> {
  return {
    'access-control-allow-origin': '*',
    'access-control-allow-headers': '*',
    'access-control-allow-methods': 'GET,POST,PUT,OPTIONS',
  };
}
