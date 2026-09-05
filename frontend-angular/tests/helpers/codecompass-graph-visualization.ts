import { expect, type Page, type Request, type Route } from '@playwright/test';

export const LOCAL_GRAPH_TEST_TOKEN = [
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9',
  'eyJzdWIiOiJjY2d2LWUyZS11c2VyIiwicm9sZSI6ImFkbWluIiwidXNlcm5hbWUiOiJjY2d2LWUyZSJ9',
  'dGVzdC1vbmx5LXNpZ25hdHVyZQ',
].join('.');

export const GRAPH_HUB_URL = 'http://127.0.0.1:5000';
export const GRAPH_PROJECT_ID = 'project-ccgv-e2e';
export const GRAPH_CONNECTION_ID = 'connection-ccgv-e2e';
export const GRAPH_KNOWLEDGE_INDEX_ID = 'index-ccgv-e2e';
export const GRAPH_TOTAL_NODE_COUNT = 12;
export const GRAPH_TOTAL_EDGE_COUNT = 18;
export const GRAPH_PARTIAL_TOPOLOGY_WARNING =
  'The semantic graph reached its configured record budget; the topology is a documented partial view.';

const SOURCE_CONTROL_RESPONSE_SCHEMA = 'ananta.source-control.api-response.v1';
const SOURCE_CONTROL_PROJECTION_PAGE_SCHEMA = 'ananta.source-control.projection-page.v1';

export type GraphArtifact = {
  schema: 'domain_graph_artifact.v1';
  source_kind: string;
  source_ref: 'unverified';
  graph_revision: string;
  metric_capabilities: Record<string, Record<string, unknown>>;
  metadata: Record<string, unknown>;
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
  warnings: string[];
  text_alternative: string;
  artifact_status: Record<string, unknown>;
};

export const GRAPH_XSS_LABEL = '<img src=x onerror="window.__ccgvXssExecuted=true">';
export const GRAPH_XSS_RELATION = 'unknown"><svg onload="window.__ccgvXssExecuted=true">';

export function createFunctionalGraphArtifact(): GraphArtifact {
  const nodes = [
    {
      node_id: 'alpha-entry',
      node_type: 'python_function',
      metrics: { in_degree: 0, out_degree: 2, total_degree: 2, code_extent: 42 },
      attributes: {
        name: GRAPH_XSS_LABEL,
        file: 'src/alpha/entry.py',
        domain_id: 'domain-alpha',
        domain_path: 'domain-alpha',
      },
    },
    {
      node_id: 'alpha-worker',
      node_type: 'python_class',
      metrics: { in_degree: 1, out_degree: 1, total_degree: 2, code_extent: 180 },
      attributes: {
        name: 'Alpha Worker',
        file: 'src/alpha/worker.py',
        domain_id: 'domain-alpha',
        domain_path: 'domain-alpha',
      },
    },
    {
      node_id: 'beta-adapter',
      node_type: 'typescript_class',
      metrics: { in_degree: 2, out_degree: 1, total_degree: 3, usage_frequency: 0 },
      attributes: {
        name: 'Beta Adapter',
        file: 'src/beta/adapter.ts',
        domain_id: 'domain-beta',
        domain_path: 'domain-beta',
      },
    },
    {
      node_id: 'unknown-node',
      node_type: 'future_language_symbol',
      raw_node_type: 'future_language_symbol',
      known_kind: false,
      metrics: { in_degree: 1, out_degree: 0, total_degree: 1 },
      attributes: {
        name: 'Unknown node kind',
        file: 'src/beta/unknown.future',
        domain_id: 'domain-beta',
        domain_path: 'domain-beta',
        semantic_status: 'semantically_unknown',
      },
    },
  ];
  const edges = [
    {
      edge_id: 'edge-call',
      source_id: 'alpha-entry',
      target_id: 'alpha-worker',
      relation: 'calls_probable_target',
      metrics: { confidence: 0, multiplicity: 1, dependency_weight: 3 },
      attributes: { confidence: 0, multiplicity: 1, directed: true },
    },
    {
      edge_id: 'edge-import',
      source_id: 'alpha-worker',
      target_id: 'beta-adapter',
      relation: 'imports_symbol',
      metrics: { confidence: 0.8, multiplicity: 2, dependency_weight: 5 },
      attributes: { confidence: 0.8, multiplicity: 2, directed: true },
    },
    {
      edge_id: 'edge-unknown',
      source_id: 'beta-adapter',
      target_id: 'unknown-node',
      relation: GRAPH_XSS_RELATION,
      raw_edge_type: GRAPH_XSS_RELATION,
      known_relation: false,
      metrics: { confidence: 0.5, multiplicity: 1 },
      attributes: {
        confidence: 0.5,
        multiplicity: 1,
        directed: true,
        semantic_status: 'semantically_unknown',
      },
    },
    {
      source_id: 'alpha-entry',
      target_id: 'beta-adapter',
      relation: 'imports_symbol',
      metrics: { confidence: 1, multiplicity: 1, dependency_weight: 2 },
      attributes: { confidence: 1, multiplicity: 1, directed: true },
    },
    {
      source_id: 'alpha-entry',
      target_id: 'beta-adapter',
      relation: 'imports_symbol',
      metrics: { confidence: 1, multiplicity: 1, dependency_weight: 2 },
      attributes: { confidence: 1, multiplicity: 1, directed: true },
    },
  ];

  return {
    schema: 'domain_graph_artifact.v1',
    source_kind: 'e2e_fixture',
    source_ref: 'unverified',
    graph_revision: 'ccgv-functional-revision-v1',
    metric_capabilities: {
      in_degree: capability('node'),
      out_degree: capability('node'),
      total_degree: capability('node'),
      code_extent: capability('node'),
      usage_frequency: capability('node', 'approximate'),
      dependency_weight: capability('edge', 'approximate'),
    },
    metadata: {
      source_kind: 'e2e_fixture',
      source_ref: 'unverified',
      graph_revision: 'ccgv-functional-revision-v1',
      node_count: nodes.length,
      edge_count: edges.length,
      view: 'topology',
      total_nodes: GRAPH_TOTAL_NODE_COUNT,
      total_edges: GRAPH_TOTAL_EDGE_COUNT,
      source_edge_count: GRAPH_TOTAL_EDGE_COUNT,
      unresolved_edge_count: 1,
      internal_edge_count: edges.length,
      edge_capped: false,
      max_edges: 400,
      semantic_budget: {
        truncated: true,
        record_limit: 30,
        record_count: 30,
        unresolved_edge_count: 1,
      },
    },
    nodes,
    edges,
    warnings: [
      'Partial metrics are rendered with explicit availability.',
      GRAPH_PARTIAL_TOPOLOGY_WARNING,
    ],
    text_alternative:
      `Topology graph window with ${nodes.length} nodes and ${edges.length} edges out of ${GRAPH_TOTAL_NODE_COUNT} nodes.`,
    artifact_status: {
      state: 'available',
      reason_code: null,
      knowledge_index_id: GRAPH_KNOWLEDGE_INDEX_ID,
      manifest_present: true,
    },
  };
}

function capability(entity: 'node' | 'edge', availability = 'available'): Record<string, unknown> {
  return {
    entity,
    availability,
    source: 'e2e_fixture',
    algorithm_version: 'e2e.v1',
  };
}

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    headers: {
      'access-control-allow-origin': '*',
      'cache-control': 'no-store',
    },
    body: JSON.stringify(body),
  });
}

function sourceControlEnvelope(data: unknown): Record<string, unknown> {
  return {
    schema: SOURCE_CONTROL_RESPONSE_SCHEMA,
    data,
  };
}

function sourceControlConnectionPage(): Record<string, unknown> {
  return {
    schema: SOURCE_CONTROL_PROJECTION_PAGE_SCHEMA,
    items: [
      {
        schema: 'ananta.source-control.projection.v1',
        connection_id: GRAPH_CONNECTION_ID,
        etag: 'a'.repeat(64),
        connection: {
          connection_id: GRAPH_CONNECTION_ID,
          project_id: GRAPH_PROJECT_ID,
          display_name: 'CodeCompass functional E2E fixture',
        },
        revision: null,
        admission: null,
        index: {
          knowledge_index_id: GRAPH_KNOWLEDGE_INDEX_ID,
          source_revision_id: 'unverified',
          status: 'ready',
        },
        active_index: {
          connection_id: GRAPH_CONNECTION_ID,
          source_revision_id: 'unverified',
          knowledge_index_id: GRAPH_KNOWLEDGE_INDEX_ID,
          generation: 1,
          status: 'active',
        },
        stale: false,
        grants: [],
        health: { state: 'ready' },
        next_actions: [],
      },
    ],
    next_cursor: null,
  };
}

function hasExpectedConnectionQuery(url: URL): boolean {
  return ['1', '200'].includes(url.searchParams.get('limit') ?? '')
    && url.searchParams.get('project_id') === GRAPH_PROJECT_ID;
}

function hasExpectedGraphQuery(url: URL): boolean {
  return url.searchParams.get('limit') === '100'
    && url.searchParams.get('view') === 'topology'
    && url.searchParams.get('max_edges') === '400'
    && url.searchParams.get('project_id') === GRAPH_PROJECT_ID;
}

async function rejectSourceControlQuery(route: Route): Promise<void> {
  await fulfillJson(route, {
    schema: 'ananta.source-control.error.v1',
    error: { code: 'ccgv_e2e_source_control_query_mismatch' },
  }, 400);
}

export async function installLocalGraphIdentity(page: Page): Promise<void> {
  await page.addInitScript(({ token, hubUrl }) => {
    localStorage.setItem('ananta.user.token', token);
    localStorage.setItem('ananta.shell.mode', 'advanced');
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', url: hubUrl, token, role: 'hub' },
    ]));
    Object.defineProperty(window, '__ccgvXssExecuted', {
      configurable: true,
      writable: true,
      value: false,
    });
  }, { token: LOCAL_GRAPH_TEST_TOKEN, hubUrl: GRAPH_HUB_URL });
}

/**
 * Fulfil every fetch/XHR made by the Internals route. Static Angular resources
 * still come from the Playwright web server; no backend process is contacted.
 */
export async function installGraphApiMocks(page: Page, artifact: unknown): Promise<void> {
  await page.route('**/*', async route => {
    const request = route.request();
    if (!['fetch', 'xhr'].includes(request.resourceType())) {
      await route.continue();
      return;
    }

    const url = new URL(request.url());
    const pathname = url.pathname;
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: { 'access-control-allow-origin': '*' } });
      return;
    }
    if (request.method() === 'GET' && pathname === '/api/projects') {
      await fulfillJson(route, {
        items: [
          {
            id: GRAPH_PROJECT_ID,
            name: 'CodeCompass E2E',
            description: 'Deterministic functional graph fixture',
            status: 'active',
            is_active: true,
            origin: 'native',
            team_id: null,
            version: 1,
            created_at: 1,
            updated_at: 1,
            archived_at: null,
          },
        ],
        count: 1,
      });
      return;
    }
    if (request.method() === 'GET' && pathname === '/api/source-control/v1/connections') {
      if (!hasExpectedConnectionQuery(url)) {
        await rejectSourceControlQuery(route);
        return;
      }
      await fulfillJson(route, sourceControlEnvelope(sourceControlConnectionPage()));
      return;
    }
    if (
      request.method() === 'GET'
      && pathname === `/api/source-control/v1/connections/${GRAPH_CONNECTION_ID}/graph`
    ) {
      if (!hasExpectedGraphQuery(url)) {
        await rejectSourceControlQuery(route);
        return;
      }
      await fulfillJson(route, sourceControlEnvelope(artifact));
      return;
    }
    if (pathname === '/api/workers') {
      await fulfillJson(route, { data: { items: [] } });
      return;
    }
    if (pathname === '/tasks/autopilot/status') {
      await fulfillJson(route, {
        data: {
          running: false,
          goal: '',
          team_id: '',
          started_at: null,
          tick_count: 0,
          dispatched_count: 0,
          completed_count: 0,
          failed_count: 0,
          last_error: null,
          effective_security_policy: {
            level: 'safe',
            max_concurrency_cap: 1,
            allowed_tool_classes: [],
          },
          circuit_breakers: { open_workers: [], open_count: 0, failure_streak: {} },
        },
      });
      return;
    }
    if (pathname === '/api/visual-process/presets' || pathname === '/api/visual-process/skill-profiles') {
      await fulfillJson(route, []);
      return;
    }

    // The route must remain hermetic if the shell adds another read-only
    // bootstrap request. Unknown writes fail closed instead of appearing to
    // succeed.
    if (request.method() === 'GET') {
      await fulfillJson(route, { data: {} });
    } else {
      await fulfillJson(route, { error: 'ccgv_unexpected_mocked_write' }, 405);
    }
  });
}

export async function openGraphInternals(page: Page): Promise<void> {
  // Load the project context before entering Internals. Source Control v1 is
  // project-bound and its interceptor deliberately rejects a missing context
  // before any HTTP request can be issued.
  await page.goto(`/codehug?projectId=${encodeURIComponent(GRAPH_PROJECT_ID)}`, {
    waitUntil: 'domcontentloaded',
  });
  await expect(page.locator('#global-project-select')).toHaveValue(GRAPH_PROJECT_ID);
  // Stay inside the running Angular application. A document-level reload
  // resets ProjectContextService and lets the Internals component issue its
  // first project-bound request before the catalog has restored the route
  // selection.
  await page.locator('.codehug-shell-nav a[href="/codehug/internals"]').click();
  await expect(page).toHaveURL(/\/codehug\/internals(?:\?|$)/);
  await expect(page.getByTestId('codecompass-graph-viewer')).toBeVisible();
  await expect(page.locator('app-simple-graph-view')).toBeVisible();
}

export type HttpRequestTracker = {
  allSince(mark: number): string[];
  graphSince(mark: number): string[];
  mark(): number;
};

export function trackHttpRequests(page: Page): HttpRequestTracker {
  const requests: string[] = [];
  page.on('request', (request: Request) => {
    if (['fetch', 'xhr'].includes(request.resourceType())) requests.push(request.url());
  });
  return {
    mark: () => requests.length,
    allSince: mark => requests.slice(mark),
    graphSince: mark => requests.slice(mark).filter(url => {
      const path = new URL(url).pathname;
      return path === '/api/source-control/v1/connections'
        || path === `/api/source-control/v1/connections/${GRAPH_CONNECTION_ID}/graph`
        || path.startsWith('/api/codecompass/')
        || path === '/knowledge/indexes';
    }),
  };
}

export async function waitForTwoDimensionalRenderer(page: Page): Promise<void> {
  await expect(page.locator('app-graph-2d-view')).toBeVisible();
  await expect.poll(() => page.evaluate(() => {
    const host = document.querySelector('app-graph-2d-view');
    const ng = (window as unknown as { ng?: { getComponent(element: Element): any } }).ng;
    return Boolean(host && ng?.getComponent(host)?.cy);
  }), { timeout: 60_000 }).toBe(true);
  await expect(page.locator('app-graph-2d-view .cy-container canvas').first()).toBeVisible();
}

export async function expectNoGraphHttpSince(tracker: HttpRequestTracker, mark: number): Promise<void> {
  expect(tracker.graphSince(mark), 'graph visual interactions must stay client-local').toEqual([]);
}

declare global {
  interface Window {
    __ccgvXssExecuted?: boolean;
  }
}
