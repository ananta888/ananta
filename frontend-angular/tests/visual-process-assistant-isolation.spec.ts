import { expect, test, type Page, type Route } from '@playwright/test';

const GRAPH_ID = 'graph-vp-assistant-isolation-e2e';
const STEP_ID = 'step-isolated';
const BASE_HASH = 'a'.repeat(64);
const LOCAL_TEST_TOKEN = [
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9',
  'eyJzdWIiOiJ2cGEtZTJlLXVzZXIiLCJyb2xlIjoiYWRtaW4iLCJ1c2VybmFtZSI6InZwYS1lMmUifQ',
  'dGVzdC1vbmx5LXNpZ25hdHVyZQ',
].join('.');

type JsonRecord = Record<string, unknown>;

function graphFixture(): JsonRecord {
  return {
    id: GRAPH_ID,
    name: 'Assistant Isolation Journey',
    description: 'Derselbe Graph wird in getrennten Angular-Instanzen geöffnet.',
    version: '1.0',
    graph_schema_version: '1',
    node_registry_version: '1.0.0',
    definition_revision: 3,
    base_graph_hash: BASE_HASH,
    tags: ['e2e'],
    metadata: {},
    steps: [{
      id: STEP_ID,
      label: 'Isolierter Node',
      kind: 'patch_propose',
      io: { inputs: [], outputs: [] },
      position: { x: 110, y: 90 },
      policy_hints: [],
      gate: false,
      metadata: {},
    }],
    edges: [],
  };
}

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function installLocalTestIdentity(page: Page): Promise<void> {
  await page.addInitScript(token => {
    localStorage.setItem('ananta.user.token', token);
    localStorage.setItem('ananta.shell.mode', 'advanced');
  }, LOCAL_TEST_TOKEN);
}

async function installEditorMocks(page: Page, instance: 'A' | 'B'): Promise<void> {
  const contextId = `ctx-sha256:${(instance === 'A' ? 'b' : 'c').repeat(64)}`;
  const conversationId = `conversation-isolation-${instance.toLowerCase()}`;

  await page.route('**/config/model-routing/profiles', route => fulfillJson(route, {
    profiles: [], fallback_groups: {}, status: 'ready',
  }));
  await page.route('**/api/ml-intern-training/**', route => {
    const path = new URL(route.request().url()).pathname;
    return fulfillJson(route, path.endsWith('/capabilities')
      ? { gpu_profiles: [], base_models: [] }
      : { items: [], next_cursor: null, total: 0 });
  });
  await page.route('**/api/visual-process/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (method === 'GET' && path.endsWith('/assistant/v1/capabilities')) {
      await fulfillJson(route, {
        contract_version: 'ananta.visual_process.assistant.capabilities.v1',
        registry_inspector: true,
        hover_help: true,
        assistant_chat: true,
        ai_patches: true,
        limits: {},
      });
      return;
    }
    if (method === 'POST' && path.endsWith('/assistant/v1/contexts')) {
      const body = request.postDataJSON() as JsonRecord;
      const editorMode = String(body['editor_mode'] ?? 'editor');
      await fulfillJson(route, {
        context_id: contextId,
        graph_id: GRAPH_ID,
        definition_revision: 3,
        definition_hash: BASE_HASH,
        editor_mode: editorMode,
        locale: 'de',
        context: {
          contract_version: 'ananta.visual_process.editor_context.v1',
          graph_id: GRAPH_ID,
          repository_revision: String(body['repository_revision'] ?? 'unverified'),
          codecompass_manifest_hash: String(body['codecompass_manifest_hash'] ?? 'unverified'),
          source_allowlist_version: String(body['source_allowlist_version'] ?? 'unverified'),
          prompt_version: 'visual-process-assistant.v1',
          graph_schema_version: '1',
          node_registry_version: '1.0.0',
          definition_revision: 3,
          definition_hash: BASE_HASH,
          draft_hash: (instance === 'A' ? 'd' : 'e').repeat(64),
          editor_mode: editorMode,
          locale: 'de',
          location: body['location'],
          graph_excerpt: {},
          effective_configuration: {},
          validation_issues: [],
          evidence_refs: [],
          allowed_mutations: [],
          extensions: {},
        },
        created_at: 1,
      }, 201);
      return;
    }
    if (method === 'POST' && path.endsWith('/assistant/v1/conversations')) {
      await fulfillJson(route, {
        conversation_id: conversationId,
        graph_id: GRAPH_ID,
        status: 'active',
        active_context_id: contextId,
        created_at: 1,
        updated_at: 1,
        requests: [],
      }, 201);
      return;
    }
    if (method === 'POST' && path.endsWith('/questions')) {
      await fulfillJson(route, {
        request_id: `request-isolation-${instance.toLowerCase()}`,
        conversation_id: conversationId,
        context_id: contextId,
        prompt_context_id: contextId,
        prompt_version: 'visual-process-assistant.v1',
        client_request_id: `client-isolation-${instance.toLowerCase()}`,
        status: 'completed',
        response: {
          contract_version: 'ananta.visual_process.help_response.v1',
          summary: `Antwort ausschließlich für Instanz ${instance}`,
          location: { target_kind: 'node', graph_id: GRAPH_ID, entity_id: STEP_ID },
          explanation: `Diese Unterhaltung gehört zur Angular-Instanz ${instance}.`,
          options: [],
          warnings: [],
          next_actions: [],
          evidence: [],
          context_id: contextId,
          prompt_version: 'visual-process-assistant.v1',
        },
        created_at: 1,
        updated_at: 2,
      }, 202);
      return;
    }
    if (method === 'GET' && path.endsWith(`/graphs/${GRAPH_ID}`)) {
      await fulfillJson(route, graphFixture());
      return;
    }
    if (method === 'GET' && path.endsWith('/graphs')) {
      await fulfillJson(route, [{
        id: GRAPH_ID,
        name: 'Assistant Isolation Journey',
        description: 'E2E-Isolationsgraph',
        tags: ['e2e'],
        version: '1.0',
        created_at: 1,
        updated_at: 2,
      }]);
      return;
    }
    if (method === 'GET' && path.endsWith('/node-definitions')) {
      await fulfillJson(route, {
        schema: 'ananta.visual_process.node_definition_registry.v1',
        registry_version: '1.0.0',
        definitions: [],
      });
      return;
    }
    if (method === 'GET' && (path.endsWith('/presets') || path.endsWith('/skill-profiles') || path.endsWith('/task-kinds'))) {
      await fulfillJson(route, []);
      return;
    }
    if (method === 'POST' && path.endsWith('/policy-summary')) {
      await fulfillJson(route, { summary: {}, per_step: {} });
      return;
    }
    await fulfillJson(route, {});
  });
}

async function installReadOnlyProcessMocks(page: Page): Promise<void> {
  const session = {
    id: 'session-readonly-e2e',
    name: 'Read-only Process Session',
    icon: '🐍',
    group: '',
    folder_id: '',
    session_type: 'general',
    session_subtype: '',
    type_description: '',
    last_message_preview: '',
    message_count: 0,
    system_prompt: '',
    settings: {},
    settings_delta: {},
    profile_id: 'general',
    process_ref: { graph_id: GRAPH_ID, version: '1.0' },
  };
  await page.route('**/api/chat/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/api/chat/sessions')) {
      await fulfillJson(route, [session]);
      return;
    }
    if (path.endsWith('/api/chat/settings/schema')) {
      await fulfillJson(route, { schema_version: 1, settings: [] });
      return;
    }
    if (path.endsWith('/sessions/session-readonly-e2e/process/runs')) {
      await fulfillJson(route, []);
      return;
    }
    if (path.endsWith('/sessions/session-readonly-e2e/process')) {
      await fulfillJson(route, {
        process_ref: { graph_id: GRAPH_ID, version: '1.0' },
        source: 'session_override',
        graph: graphFixture(),
        run: null,
      });
      return;
    }
    await fulfillJson(route, []);
  });
}

async function loadIsolationGraph(page: Page): Promise<void> {
  await page.goto('/process-designer', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.vpe-canvas-wrap')).toBeVisible();
  await page.getByRole('button', { name: 'Geladen ▾' }).click();
  await page.getByRole('button', { name: /Assistant Isolation Journey/ }).click();
  await expect(page.locator(`[data-semantic-kind="node"][data-entity-id="${STEP_ID}"]`)).toBeVisible();
}

test('zwei Tabs bleiben zustandsisoliert; die AI-Snake-Prozessansicht mutiert im Read-only-Modus nichts', async ({ page, context }) => {
  await installEditorMocks(page, 'A');
  await installLocalTestIdentity(page);
  await loadIsolationGraph(page);

  const secondPage = await context.newPage();
  await installEditorMocks(secondPage, 'B');
  await installReadOnlyProcessMocks(secondPage);
  await secondPage.addInitScript(sessionId => {
    localStorage.setItem('ananta.snake.session', sessionId);
  }, 'session-readonly-e2e');
  await loadIsolationGraph(secondPage);

  const firstNode = page.locator(`[data-semantic-kind="node"][data-entity-id="${STEP_ID}"]`);
  const secondNode = secondPage.locator(`[data-semantic-kind="node"][data-entity-id="${STEP_ID}"]`);
  await firstNode.focus();
  await firstNode.press('Enter');
  const firstLabel = page.locator('[data-field-path="/label"] input');
  await firstLabel.fill('Nur Instanz A');
  await expect(firstLabel).toHaveValue('Nur Instanz A');
  await expect(secondNode.locator('.vpe-node-label')).toHaveText('Isolierter Node');

  const question = page.getByLabel('Frage zu diesem Kontext');
  await question.fill('Welche Instanz beantwortet diese Frage?');
  await page.getByRole('button', { name: 'Fragen' }).click();
  await expect(page.locator('.vp-assistant-bubble')).toContainText('Antwort ausschließlich für Instanz A');
  await expect(secondPage.locator('.vp-assistant-bubble')).toHaveCount(0);

  const storageKey = `ananta.visual-process.assistant.conversation.v1:${GRAPH_ID}`;
  expect(await page.evaluate(key => sessionStorage.getItem(key), storageKey)).toBe('conversation-isolation-a');
  expect(await secondPage.evaluate(key => sessionStorage.getItem(key), storageKey)).toBeNull();

  const forbiddenMutations: string[] = [];
  secondPage.on('request', current => {
    const path = new URL(current.url()).pathname;
    if (current.method() === 'GET') return;
    if (path.includes('/api/visual-process/v2/graphs/')
      || path.endsWith('/api/visual-process/graphs')
      || path.includes('/patch-decisions')) {
      forbiddenMutations.push(`${current.method()} ${path}`);
    }
  });
  const assistantLauncher = secondPage.getByTestId('assistant-dock-launcher');
  if (await assistantLauncher.isVisible().catch(() => false)) await assistantLauncher.click();
  const openAssistant = secondPage.getByRole('button', { name: 'Assistant oeffnen' });
  if (await openAssistant.isVisible().catch(() => false)) await openAssistant.click();
  const openSnakeChat = secondPage.locator('[data-waypoint="assistant.tab-chat"]');
  await expect(openSnakeChat).toBeVisible();
  await openSnakeChat.click();
  const processTab = secondPage
    .getByTestId('assistant-snake-chat-panel')
    .getByRole('button', { name: 'Prozess', exact: true });
  await processTab.click();

  const readOnlyPanel = secondPage.locator('.process-panel');
  await expect(readOnlyPanel).toBeVisible();
  await expect(readOnlyPanel).toContainText(`${GRAPH_ID} · Version 1.0`);
  const readOnlyNode = readOnlyPanel.locator(`[data-semantic-kind="node"][data-entity-id="${STEP_ID}"]`);
  await expect(readOnlyNode).toBeVisible();
  await expect(readOnlyPanel.locator('.vpe-canvas-wrap')).toHaveClass(/readonly/);
  await expect(readOnlyPanel.getByRole('button', { name: /Speichern|Schritt/ })).toHaveCount(0);

  await readOnlyNode.focus();
  await readOnlyNode.press('Delete');
  await expect(readOnlyNode.locator('.vpe-node-label')).toHaveText('Isolierter Node');
  await expect(readOnlyPanel.locator('app-vp-step-inspector input, app-vp-step-inspector textarea, app-vp-step-inspector select')).toHaveCount(0);
  expect(forbiddenMutations).toEqual([]);

  await secondPage.close();
});
