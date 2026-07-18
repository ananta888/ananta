import { expect, test, type Page, type Route } from '@playwright/test';

const GRAPH_ID = 'graph-vp-assistant-patch-e2e';
const STEP_ID = 'step-patch-proposal';
const BASE_HASH = '1'.repeat(64);
const PATCH_HASH = '2'.repeat(64);
const PREVIEW_HASH = '3'.repeat(64);
const SAVED_HASH = '4'.repeat(64);
const CONTEXT_ID = `ctx-sha256:${'5'.repeat(64)}`;
const LOCAL_TEST_TOKEN = [
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9',
  'eyJzdWIiOiJ2cGEtZTJlLXVzZXIiLCJyb2xlIjoiYWRtaW4iLCJ1c2VybmFtZSI6InZwYS1lMmUifQ',
  'dGVzdC1vbmx5LXNpZ25hdHVyZQ',
].join('.');

type JsonRecord = Record<string, unknown>;
type JourneyMode = 'fail-closed-unverified' | 'ui-mechanics-only';
type JourneyCapture = {
  contextGraph?: JsonRecord;
  cas?: { headers: Record<string, string>; body: JsonRecord };
  previewRequests: number;
  decisionRequests: number;
};

function graphFixture(): JsonRecord {
  return {
    id: GRAPH_ID,
    name: 'Assistant Patch Journey',
    description: 'Revisionierter Graph für die atomare Patch-Journey.',
    version: '1.0',
    graph_schema_version: '1',
    node_registry_version: '1.0.0',
    definition_revision: 7,
    base_graph_hash: BASE_HASH,
    tags: ['e2e'],
    metadata: {},
    steps: [{
      id: STEP_ID,
      label: 'Patch prüfen',
      kind: 'patch_propose',
      io: { inputs: [], outputs: [] },
      position: { x: 120, y: 100 },
      policy_hints: [],
      gate: false,
      metadata: { description: 'Lokale Ausgangskonfiguration' },
    }],
    edges: [],
  };
}

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

async function installLocalTestIdentity(page: Page): Promise<void> {
  await page.addInitScript(token => {
    localStorage.setItem('ananta.user.token', token);
    localStorage.setItem('ananta.shell.mode', 'advanced');
  }, LOCAL_TEST_TOKEN);
}

async function installPatchJourneyMocks(page: Page, capture: JourneyCapture, mode: JourneyMode): Promise<void> {
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
      capture.contextGraph = clone(body['draft_graph'] as JsonRecord);
      const editorMode = String(body['editor_mode'] ?? 'editor');
      const location = body['location'] as JsonRecord;
      await fulfillJson(route, {
        context_id: CONTEXT_ID,
        graph_id: GRAPH_ID,
        definition_revision: 7,
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
          definition_revision: 7,
          definition_hash: BASE_HASH,
          draft_hash: '6'.repeat(64),
          editor_mode: editorMode,
          locale: 'de',
          location,
          graph_excerpt: {},
          effective_configuration: {},
          validation_issues: [],
          evidence_refs: [],
          allowed_mutations: ['update_step_field'],
          extensions: {},
        },
        created_at: 1,
      }, 201);
      return;
    }
    if (method === 'POST' && path.endsWith('/assistant/v1/conversations')) {
      await fulfillJson(route, {
        conversation_id: 'conversation-patch-e2e',
        graph_id: GRAPH_ID,
        status: 'active',
        active_context_id: CONTEXT_ID,
        created_at: 1,
        updated_at: 1,
        requests: [],
      }, 201);
      return;
    }
    if (method === 'POST' && path.endsWith('/questions')) {
      const evidence = {
        evidence_id: 'fixture-evidence-missing-source-id',
        verification_status: 'unverified',
        trust_level: 'inferred',
        path: 'agent/services/visual_process.py',
        line_start: 10,
        reason_codes: ['source_id_missing'],
      };
      await fulfillJson(route, {
        request_id: 'request-patch-e2e',
        conversation_id: 'conversation-patch-e2e',
        context_id: CONTEXT_ID,
        prompt_context_id: CONTEXT_ID,
        prompt_version: 'visual-process-assistant.v1',
        client_request_id: 'client-patch-e2e',
        status: 'completed',
        error_code: mode === 'fail-closed-unverified' ? 'no_results' : null,
        response: {
          contract_version: 'ananta.visual_process.help_response.v1',
          summary: 'Die Node-Konfiguration kann nachvollziehbar angepasst werden.',
          location: { target_kind: 'node', graph_id: GRAPH_ID, entity_id: STEP_ID },
          explanation: 'Der Vorschlag bleibt bis zur expliziten Bestätigung unverändert.',
          options: ['Label gezielt anpassen'],
          warnings: mode === 'fail-closed-unverified'
            ? ['Der Fixture-Beleg besitzt absichtlich keine verifizierte Source-ID.']
            : ['Reiner UI-Mechanik-Fixture; keine Grounding- oder QA001-Evidence-Behauptung.'],
          next_actions: ['Patch prüfen'],
          evidence: mode === 'fail-closed-unverified' ? [evidence] : [],
          context_id: CONTEXT_ID,
          prompt_version: 'visual-process-assistant.v1',
          workflow_patch: {
            contract_version: 'ananta.visual_process.workflow_patch.v1',
            graph_id: GRAPH_ID,
            definition_revision: 7,
            base_graph_hash: BASE_HASH,
            operations: [{
              operation_id: 'operation-update-label',
              op: 'update_step_field',
              step_id: STEP_ID,
              path: '/label',
              expected_old_value: 'Vom Nutzer konfiguriert',
              value: 'Vom AI-Patch bestätigt',
              evidence_refs: mode === 'fail-closed-unverified' ? [evidence.evidence_id] : [],
            }],
            evidence_refs: mode === 'fail-closed-unverified' ? [evidence.evidence_id] : [],
            extensions: { fixture_scope: 'ui_mechanics_only_not_grounding_evidence' },
          },
        },
        created_at: 1,
        updated_at: 2,
      }, 202);
      return;
    }
    if (method === 'POST' && path.endsWith('/requests/request-patch-e2e/patch-preview')) {
      capture.previewRequests += 1;
      const previewGraph = clone(capture.contextGraph ?? graphFixture());
      const steps = previewGraph['steps'] as JsonRecord[];
      steps[0] = { ...steps[0], label: 'Vom AI-Patch bestätigt' };
      await fulfillJson(route, {
        patch_hash: PATCH_HASH,
        base_graph_hash: BASE_HASH,
        preview_graph_hash: PREVIEW_HASH,
        preview_graph: previewGraph,
        validation: { valid: true, error_count: 0, warning_count: 0, issues: [] },
        operation_count: 1,
        audit_id: 'audit-patch-e2e',
        decision: 'approved',
      });
      return;
    }
    if (method === 'POST' && path.endsWith('/requests/request-patch-e2e/patch-decisions')) {
      capture.decisionRequests += 1;
      await fulfillJson(route, {
        audit_id: 'audit-patch-e2e',
        request_id: 'request-patch-e2e',
        patch_hash: PATCH_HASH,
        decision: 'accepted',
        apply_mode: 'local_editor_command_only',
        preview: {},
      });
      return;
    }
    if (method === 'PUT' && path.endsWith(`/v2/graphs/${GRAPH_ID}`)) {
      capture.cas = {
        headers: request.headers(),
        body: request.postDataJSON() as JsonRecord,
      };
      await fulfillJson(route, {
        id: GRAPH_ID,
        version: '1.0',
        graph_schema_version: '1',
        node_registry_version: '1.0.0',
        definition_revision: 8,
        base_graph_hash: SAVED_HASH,
        saved: true,
        changed: true,
      });
      return;
    }
    if (method === 'GET' && path.endsWith(`/graphs/${GRAPH_ID}`)) {
      await fulfillJson(route, graphFixture());
      return;
    }
    if (method === 'GET' && path.endsWith('/graphs')) {
      await fulfillJson(route, [{
        id: GRAPH_ID,
        name: 'Assistant Patch Journey',
        description: 'Revisionierter E2E-Graph',
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
        registry_hash: 'fixture-empty-registry',
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

async function openConfiguredGraphAndAsk(page: Page, questionText: string) {
  await page.goto('/process-designer', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.vpe-canvas-wrap')).toBeVisible();
  await page.getByRole('button', { name: 'Geladen ▾' }).click();
  await page.getByRole('button', { name: /Assistant Patch Journey/ }).click();
  const node = page.locator(`[data-semantic-kind="node"][data-entity-id="${STEP_ID}"]`);
  await expect(node).toBeVisible();
  await node.focus();
  await node.press('Enter');

  const labelField = page.locator('[data-field-path="/label"] input');
  await expect(labelField).toHaveValue('Patch prüfen');
  await labelField.fill('Vom Nutzer konfiguriert');
  await expect(labelField).toHaveValue('Vom Nutzer konfiguriert');

  const question = page.getByLabel('Frage zu diesem Kontext');
  await expect(question).toBeVisible();
  await question.fill(questionText);
  await page.getByRole('button', { name: 'Fragen' }).click();
  return labelField;
}

test('unverified Evidence ohne autoritative Source-ID bleibt fail-closed', async ({ page }) => {
  const capture: JourneyCapture = { previewRequests: 0, decisionRequests: 0 };
  await installPatchJourneyMocks(page, capture, 'fail-closed-unverified');
  await installLocalTestIdentity(page);
  const labelField = await openConfiguredGraphAndAsk(page, 'Gibt es dafür eine autoritativ belegte Änderung?');

  await expect(page.locator('.request-status')).toContainText('Keine belegbaren Ergebnisse');
  await expect(page.getByRole('heading', { name: 'Belege' })).toBeVisible();
  await expect(page.getByText('fixture-evidence-missing-source-id')).toBeVisible();
  await expect(page.locator('.evidence li')).toContainText('unverified');
  await expect(page.getByRole('button', { name: 'Sichere Vorschau öffnen' })).toHaveCount(0);
  await expect(labelField).toHaveValue('Vom Nutzer konfiguriert');
  expect(((capture.contextGraph?.['steps'] as JsonRecord[])?.[0]?.['label'])).toBe('Vom Nutzer konfiguriert');
  expect(capture.previewRequests).toBe(0);
  expect(capture.decisionRequests).toBe(0);
  expect(capture.cas).toBeUndefined();
});

test('UI-Mechanik-Fixture ohne Grounding-Behauptung: Patch-Bestätigung → Undo/Redo → CAS-Save', async ({ page }) => {
  const capture: JourneyCapture = { previewRequests: 0, decisionRequests: 0 };
  await installPatchJourneyMocks(page, capture, 'ui-mechanics-only');
  await installLocalTestIdentity(page);
  const labelField = await openConfiguredGraphAndAsk(page, 'Lokale UI-Mechanik des Patch-Dialogs prüfen');

  await expect(page.locator('.request-status')).toContainText('Antwort vollständig');
  await expect(page.getByText('Reiner UI-Mechanik-Fixture; keine Grounding- oder QA001-Evidence-Behauptung.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Belege' })).toHaveCount(0);
  expect(((capture.contextGraph?.['steps'] as JsonRecord[])?.[0]?.['label'])).toBe('Vom Nutzer konfiguriert');

  await page.getByRole('button', { name: 'Sichere Vorschau öffnen' }).click();
  const patchDialog = page.getByRole('dialog', { name: 'AI-Patch-Vorschau' });
  await expect(patchDialog).toBeVisible();
  await expect(patchDialog).toContainText('Vom Nutzer konfiguriert → Vom AI-Patch bestätigt');
  await expect(patchDialog.getByTestId('vp-patch-policy')).toContainText('approved');
  await expect(patchDialog).toContainText('audit-patch-e2e');

  await patchDialog.getByLabel('Vollständigen Patch atomar in den lokalen Draft übernehmen').check();
  await patchDialog.getByRole('button', { name: 'Bestätigt übernehmen' }).click();
  await expect(patchDialog).toContainText('Patch als eine lokale Editor-Transaktion übernommen');
  await patchDialog.getByRole('button', { name: 'Vorschau schließen' }).click();
  await expect(labelField).toHaveValue('Vom AI-Patch bestätigt');

  await page.getByRole('button', { name: '↶' }).click();
  await expect(labelField).toHaveValue('Vom Nutzer konfiguriert');
  await page.getByRole('button', { name: '↷' }).click();
  await expect(labelField).toHaveValue('Vom AI-Patch bestätigt');

  await page.getByRole('button', { name: /Speichern/ }).click();
  await expect(page.locator('.vpe-status-msg')).toContainText('Gespeichert');
  await expect.poll(() => capture.cas).toBeTruthy();
  expect(capture.cas?.headers['if-match']).toBe(`"${BASE_HASH}"`);
  expect(capture.cas?.body['expected_revision']).toBe(7);
  expect(capture.cas?.body['base_graph_hash']).toBe(BASE_HASH);
  const savedGraph = capture.cas?.body['graph'] as JsonRecord;
  expect(((savedGraph['steps'] as JsonRecord[])[0]['label'])).toBe('Vom AI-Patch bestätigt');
  expect(capture.previewRequests).toBe(1);
  expect(capture.decisionRequests).toBe(1);
  await expect(page.locator('.vpe-dirty')).toHaveCount(0);
});
