// Synthetic, isolated component checks with real bpmn-js/moddle and Chromium.
// Hub API responses are deterministic doubles; this is not Hub/Worker release evidence.
// Run: node scripts/test-bpmn-browser.mjs (uses installed dependencies, no server).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';

const root = fileURLToPath(new URL('../', import.meta.url));
const bundle = await build({
  absWorkingDir: root,
  stdin: {
    contents: `
      import '@angular/compiler';
      import { bootstrapApplication } from '@angular/platform-browser';
      import { Subject, of } from 'rxjs';
      import { BpmnBlueprintEditorComponent } from './src/app/features/visual-process/bpmn-blueprint-editor.component';
      import { VisualProcessApiService } from './src/app/features/visual-process/visual-process-api.service';
      import { readBpmnMetadata, bpmnMetadataExtension } from './src/app/features/visual-process/bpmn-metadata';
      import 'bpmn-js/dist/assets/diagram-js.css';
      import 'bpmn-js/dist/assets/bpmn-js.css';
      import 'bpmn-js/dist/assets/bpmn-font/css/bpmn.css';
      const requests = { imports: [], compiles: [], preflights: [], starts: [], statuses: [], controls: [], events: [], traces: [] };
      const pending = (kind, args) => {
        const response = new Subject();
        requests[kind].push({ args, response });
        return response;
      };
      const api = {
        getBpmnCapabilities: () => of({
          schema: 'ananta.bpmn_capabilities.v1', version: 'synthetic', runtime_verified: false,
          required_feature_flag: 'ANANTA_BPMN_EXECUTION_ENABLED', required_capability: 'bpmn_control_v1',
          expression_language: 'ananta-condition-v1', runtimes: { 'ananta-native': 'opt_in_contract' },
          elements: [], unsupported: [],
        }),
        importBpmn: (...args) => pending('imports', args),
        compileWorkflowRequest: (...args) => pending('compiles', args),
        preflightWorkflowFromGraph: (...args) => pending('preflights', args),
        startWorkflowFromGraph: (...args) => pending('starts', args),
        getWorkflowStatus: (...args) => pending('statuses', args),
        getWorkflowEvents: (...args) => pending('events', args),
        getBpmnEdgeTrace: (...args) => pending('traces', args),
        controlBpmnWorkflow: (...args) => pending('controls', args),
      };
      bootstrapApplication(BpmnBlueprintEditorComponent, {
        providers: [{ provide: VisualProcessApiService, useValue: api }],
      }).then(app => {
        window.bpmnTest = { app, component: app.components[0].instance, requests, readBpmnMetadata, bpmnMetadataExtension };
      });
    `,
    resolveDir: root, loader: 'ts',
  },
  bundle: true, write: false, outfile: 'bpmn-browser.js', platform: 'browser', format: 'iife', target: 'es2022',
  loader: { '.woff2': 'dataurl', '.woff': 'dataurl', '.ttf': 'dataurl', '.eot': 'dataurl', '.svg': 'dataurl' },
  tsconfig: `${root}tsconfig.json`,
});

const validation = { valid: true, issues: [], error_count: 0, warning_count: 0 };
const imported = {
  graph: { id: 'synthetic', name: 'Synthetic', description: '', version: '1', steps: [], edges: [], tags: [] },
  validation, warnings: ['Synthetic Hub conversion warning'],
  execution_support: { schema: 'ananta.bpmn_execution_report.v1', supported: true, runtime_verified: false, issues: [] },
};
const compiled = { workflow_request: { schema: 'synthetic' }, validation, errors: [] };
const readiness = { ready: true, workflow_id: imported.graph.id, runtime_id: 'ananta-native',
  plan_hash: 'a'.repeat(64), definition_hash: 'b'.repeat(64), reason_codes: [] };
const runtime = { schema: 'synthetic', workflow_id: imported.graph.id, run_id: 'synthetic-run', backend: 'ananta-native',
  status: 'paused', plan_hash: readiness.plan_hash, definition_hash: readiness.definition_hash,
  revision: 3, checkpoint_ref: 'synthetic-checkpoint', allowed_commands: ['resume', 'retry', 'cancel'],
  steps: [{ step_id: 'Task_1', status: 'completed' }, { step_id: 'Task_2', status: 'awaiting_approval' }] };
const event = { schema: 'ananta.workflow_event.v1', event_id: 'synthetic-event', workflow_id: runtime.workflow_id,
  run_id: runtime.run_id, sequence: 1, step_id: 'Task_1', event_type: 'workflow.step.completed',
  payload: { selected_edge: 'Flow_2', hub_task_id: 'synthetic-task', variables: { secret: 'HIDDEN_RUNTIME_VALUE' } } };

async function withEditor(check) {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.abort());
    await page.setContent(`<!doctype html><html lang="de"><head><meta charset="utf-8"><title>BPMN synthetic test</title>
      <style>body { margin: 16px; font-family: system-ui; } input,textarea,select { max-width: 100%; box-sizing: border-box; }
        :root { --border: #aaa; --warning: #8c4b00; --surface-soft: #eee; }</style>
      </head><body><app-bpmn-blueprint-editor></app-bpmn-blueprint-editor></body></html>`);
    for (const output of bundle.outputFiles.filter(file => file.path.endsWith('.css'))) {
      await page.addStyleTag({ content: output.text });
    }
    await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith('.js')).text });
    await expect(page.locator('.bpmn-title')).toContainText('Diagramm geladen');
    await check(page);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

async function selectTask(page, id = 'Task_1') {
  await page.locator(`.djs-element[data-element-id="${id}"] .djs-hit`).click();
  await expect(page.getByLabel('Rolle', { exact: true })).toBeVisible();
}

async function respond(page, kind, result, index = 0, error = false) {
  await expect.poll(() => page.evaluate(kind => window.bpmnTest.requests[kind].length, kind)).toBeGreaterThan(index);
  await page.evaluate(({ kind, result, index, error }) => {
    const response = window.bpmnTest.requests[kind][index].response;
    if (error) response.error({ error: result });
    else response.next(result);
  }, { kind, result, index, error });
}

async function compile(page, index = 0) {
  await page.getByRole('button', { name: 'WorkflowRequest', exact: true }).click();
  await respond(page, 'imports', imported, index);
  await respond(page, 'compiles', compiled, index);
}

async function loadRuntime(page, status = runtime) {
  const indices = await page.evaluate(() => Object.fromEntries(Object.entries(window.bpmnTest.requests).map(([kind, list]) => [kind, list.length])));
  await page.getByLabel('Workflow-ID', { exact: true }).fill(status.workflow_id);
  await page.getByRole('button', { name: 'Status laden' }).click();
  await respond(page, 'statuses', status, indices.statuses);
  await respond(page, 'events', { events: [event, event] }, indices.events);
  await respond(page, 'traces', { schema: 'ananta.caseflow_edge_trace_read_model.v1', workflow_id: status.workflow_id,
    run_id: status.run_id, source_revision: status.revision, edges: [] }, indices.traces);
  await respond(page, 'statuses', status, indices.statuses + 1);
}

test('metadata controls, real command-stack undo/redo, XML roundtrip and new-document isolation', { timeout: 45000 }, async () => {
  await withEditor(async page => {
    await expect(page.locator('.bjs-powered-by')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
    await selectTask(page);
    await page.getByLabel('Rolle', { exact: true }).fill('analyst');
    await page.getByLabel('Task-Art').selectOption('analysis');
    await page.getByLabel('Erlaubte Tools').fill('read_file, run_tests');
    await page.getByLabel('Policy Scope JSON').fill('{"scope":"synthetic"}');
    await page.getByLabel('Gate', { exact: true }).check();
    await page.getByRole('button', { name: 'Rückgängig' }).click();
    await expect(page.getByLabel('Gate', { exact: true })).not.toBeChecked();
    await page.getByRole('button', { name: 'Wiederholen' }).click();
    await expect(page.getByLabel('Gate', { exact: true })).toBeChecked();
    await page.getByLabel('Rolle', { exact: true }).fill('reviewer');
    await page.getByRole('button', { name: 'Rückgängig' }).click();
    await expect(page.getByLabel('Rolle', { exact: true })).toHaveValue('analyst');
    await page.getByRole('button', { name: 'Wiederholen' }).click();
    await expect(page.getByLabel('Rolle', { exact: true })).toHaveValue('reviewer');
    await page.getByLabel('Name', { exact: true }).fill('Renamed');
    await page.getByRole('button', { name: 'Rückgängig' }).click();
    await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Planen');
    await page.getByRole('button', { name: 'XML', exact: true }).click();
    await expect(page.getByLabel('BPMN XML', { exact: true })).toHaveValue(/ananta:metadata/);
    const xml = await page.getByLabel('BPMN XML', { exact: true }).inputValue();
    assert.match(xml, /ananta:metadata/);
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await expect(page.getByLabel('Rolle', { exact: true })).toHaveCount(0);
    await selectTask(page);
    await expect(page.getByLabel('Rolle', { exact: true })).toHaveValue('reviewer');
    await expect(page.getByLabel('Gate', { exact: true })).toBeChecked();
    await expect(page.getByLabel('Erlaubte Tools')).toHaveValue('read_file, run_tests');
    await expect(page.getByLabel('Policy Scope JSON')).toHaveValue('{"scope":"synthetic"}');
    await page.getByRole('button', { name: 'Neu', exact: true }).click();
    await selectTask(page);
    await expect(page.getByLabel('Rolle', { exact: true })).toHaveValue('default');
    await expect(page.getByLabel('Gate', { exact: true })).not.toBeChecked();
  });
});

test('real bpmn-js preserves conditions/default flows and copies/deletes metadata through history', { timeout: 45000 }, async () => {
  await withEditor(async page => {
    const xml = await page.getByLabel('BPMN XML', { exact: true }).inputValue();
    const source = xml
      .replace('<bpmn:userTask id="Task_2" name="Freigabe" />', '<bpmn:exclusiveGateway id="Task_2" name="Choice" default="Flow_3" />')
      .replace('<bpmn:sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="Task_2" />',
        '<bpmn:sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="Task_2"><bpmn:conditionExpression>approved == true</bpmn:conditionExpression></bpmn:sequenceFlow>');
    await page.getByLabel('BPMN XML', { exact: true }).fill(source);
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await selectTask(page);
    await page.evaluate(() => {
      const { component, bpmnMetadataExtension } = window.bpmnTest;
      const modeler = component.modeler;
      const task = modeler.get('elementRegistry').get('Task_1');
      modeler.get('modeling').updateProperties(task, { extensionElements: bpmnMetadataExtension(modeler.get('moddle'), task.businessObject,
        { role: 'source', future_field: { retained: true }, io: { inputs: [{ name: 'request', kind: 'text', required: true }], outputs: [] } }) });
    });
    await page.getByLabel('Rolle', { exact: true }).fill('analyst');
    const copyId = await page.evaluate(() => {
      const modeler = window.bpmnTest.component.modeler;
      const task = modeler.get('elementRegistry').get('Task_1');
      modeler.get('copyPaste').copy([task]);
      modeler.get('copyPaste').paste({ element: task.parent, point: { x: 360, y: 400 } });
      const copy = modeler.get('elementRegistry').filter(element => element.type === 'bpmn:ServiceTask' && element.id !== 'Task_1')[0];
      modeler.get('selection').select(copy);
      return copy.id;
    });
    await expect(page.getByLabel('Rolle', { exact: true })).toHaveValue('analyst');
    await page.getByLabel('Rolle', { exact: true }).fill('copy');
    await selectTask(page);
    await expect(page.getByLabel('Rolle', { exact: true })).toHaveValue('analyst');
    await page.evaluate(copyId => {
      const modeler = window.bpmnTest.component.modeler;
      const copy = modeler.get('elementRegistry').get(copyId);
      modeler.get('selection').select(copy);
      modeler.get('modeling').removeElements([copy]);
    }, copyId);
    await expect(page.getByLabel('Rolle', { exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Rückgängig' }).click();
    await selectTask(page, copyId);
    await expect(page.getByLabel('Rolle', { exact: true })).toHaveValue('copy');
    await page.getByRole('button', { name: 'XML', exact: true }).click();
    await expect(page.getByLabel('BPMN XML', { exact: true })).toHaveValue(/ananta:metadata/);
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    const roundtrip = await page.evaluate(copyId => {
      const { component, readBpmnMetadata } = window.bpmnTest;
      const registry = component.modeler.get('elementRegistry');
      return {
        metadata: readBpmnMetadata(registry.get(copyId).businessObject),
        condition: registry.get('Flow_2').businessObject.conditionExpression.body,
        defaultFlow: registry.get('Task_2').businessObject.default.id,
      };
    }, copyId);
    assert.equal(roundtrip.metadata.role, 'copy');
    assert.deepEqual(roundtrip.metadata.future_field, { retained: true });
    assert.equal(roundtrip.metadata.io.inputs[0].name, 'request');
    assert.equal(roundtrip.condition, 'approved == true');
    assert.equal(roundtrip.defaultFlow, 'Flow_3');
  });
});

test('lossy XML import warns, blocks Start after compilation, and resets only on a clean document', { timeout: 45000 }, async () => {
  await withEditor(async page => {
    const xml = await page.getByLabel('BPMN XML', { exact: true }).inputValue();
    await page.getByLabel('BPMN XML', { exact: true }).fill(xml.replace('</bpmn:process>', '<bpmn:unknownTask id="Lost" /></bpmn:process>'));
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await expect(page.locator('.bpmn-warnings')).toContainText('möglicher Datenverlust');
    await compile(page);
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
    await expect(page.locator('.bpmn-warnings')).toContainText('unknownTask');
    await selectTask(page);
    await page.getByLabel('Rolle', { exact: true }).fill('edited');
    await expect(page.locator('.bpmn-warnings')).toContainText('unknownTask');
    await page.getByRole('button', { name: 'Neu', exact: true }).click();
    await expect(page.locator('.bpmn-warnings')).toHaveCount(0);
    await page.getByLabel('BPMN XML', { exact: true }).fill('<broken');
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await expect(page.locator('.bpmn-warnings')).toContainText('fehlgeschlagen');
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
  });
});

test('Hub support issues focus elements; compilation cannot claim runtime readiness or override Hub rejection', { timeout: 45000 }, async () => {
  await withEditor(async page => {
    await page.getByRole('button', { name: 'WorkflowRequest', exact: true }).click();
    await respond(page, 'imports', { ...imported, execution_support: { ...imported.execution_support, supported: false,
      issues: [{ element_id: 'Task_1', reason_code: 'bpmn_child_unsupported', detail: 'Synthetic unsupported child' }] } });
    await page.getByRole('button', { name: /Task_1: bpmn_child_unsupported/ }).click();
    await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Planen');
    await expect(page.locator('.djs-element[data-element-id="Task_1"]')).toHaveClass(/bpmn-unsupported/);
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
    assert.equal(await page.evaluate(() => window.bpmnTest.requests.compiles.length), 0);
    await page.getByLabel('Rolle', { exact: true }).fill('changed');
    await expect(page.locator('.djs-element[data-element-id="Task_1"]')).not.toHaveClass(/bpmn-unsupported/);
    await page.getByRole('button', { name: 'WorkflowRequest', exact: true }).click();
    await respond(page, 'imports', imported, 1);
    await respond(page, 'compiles', compiled);
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
    await respond(page, 'preflights', readiness);
    await expect(page.getByRole('status')).toContainText('Kein Ausführungsnachweis');
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Start', exact: true }).click();
    await respond(page, 'starts', { data: { reason_code: 'workflow_runtime_capability_unavailable' } }, 0, true);
    await expect(page.locator('.bpmn-shell pre')).toContainText('workflow_runtime_capability_unavailable');
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
    await expect(page.locator('.bpmn-runtime dl')).toHaveCount(0);
  });
});

test('late compile cannot enable Start after edit; runtime lookup shows only Hub fields and ignores old responses', { timeout: 45000 }, async () => {
  await withEditor(async page => {
    await selectTask(page);
    await page.getByRole('button', { name: 'WorkflowRequest', exact: true }).click();
    await respond(page, 'imports', imported);
    await page.getByLabel('Rolle', { exact: true }).fill('new revision');
    await respond(page, 'compiles', compiled);
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
    await page.getByLabel('Workflow-ID', { exact: true }).fill('old-workflow');
    await page.getByRole('button', { name: 'Status laden' }).click();
    await page.getByLabel('Workflow-ID', { exact: true }).fill('workflow');
    await respond(page, 'statuses', { schema: 'synthetic', workflow_id: 'old-workflow', backend: 'legacy', status: 'completed' });
    await expect(page.locator('.bpmn-runtime dl')).toHaveCount(0);
    await page.getByRole('button', { name: 'Status laden' }).click();
    await respond(page, 'statuses', {
      schema: 'synthetic', workflow_id: 'workflow', backend: 'legacy', status: 'running',
      variables: { secret: 'SENSITIVE_TEST_VALUE' },
      steps: [{ step_id: 'Task_1', status: 'blocked' }, { step_id: 'Task_2', status: 'skipped' }],
    }, 1);
    await expect(page.locator('.bpmn-runtime')).toContainText('kein Ausführungs- oder Release-Nachweis');
    await expect(page.locator('.bpmn-runtime tbody').first()).toContainText('blocked');
    await expect(page.locator('.bpmn-runtime tbody').first()).toContainText('skipped');
    await expect(page.locator('.bpmn-runtime')).not.toContainText('SENSITIVE_TEST_VALUE');
    await expect(page.locator('.bpmn-runtime')).toContainText('Nicht gemeldet');
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
  });
});

test('preflight denial and late readiness never enable a changed definition', { timeout: 45000 }, async () => {
  await withEditor(async page => {
    await compile(page);
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
    await respond(page, 'preflights', { ...readiness, ready: false, reason_codes: ['tenant_runtime_disabled'] });
    await expect(page.getByRole('status')).toContainText('tenant_runtime_disabled');
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
    await compile(page, 1);
    await selectTask(page);
    await page.getByLabel('Rolle', { exact: true }).fill('changed while preflight pending');
    await respond(page, 'preflights', readiness, 1);
    await expect(page.getByRole('status')).toContainText('Definition geändert');
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
  });
});

test('reloaded Hub history paints selected flow and step states only for the bound definition', { timeout: 45000 }, async () => {
  // Each withEditor bootstraps a new component/modeler; only the Hub doubles retain the run data.
  for (let reload = 0; reload < 2; reload++) await withEditor(async page => {
    await loadRuntime(page);
    await expect(page.locator('.bpmn-runtime-edges tbody tr')).toHaveCount(1);
    await expect(page.locator('.bpmn-runtime-edges')).toContainText('selected');
    await expect(page.locator('.djs-element[data-element-id="Flow_2"]')).not.toHaveClass(/bpmn-runtime-selected/);
    await compile(page);
    await respond(page, 'preflights', readiness);
    await expect(page.locator('.djs-element[data-element-id="Flow_2"]')).toHaveClass(/bpmn-runtime-selected/);
    await expect(page.locator('.djs-element[data-element-id="Task_1"]')).toHaveClass(/bpmn-runtime-completed/);
    await expect(page.locator('.djs-element[data-element-id="Task_2"]')).toHaveClass(/bpmn-runtime-waiting/);
    await expect(page.locator('.bpmn-runtime')).toContainText('synthetic-task');
    await expect(page.locator('.bpmn-runtime')).not.toContainText('HIDDEN_RUNTIME_VALUE');
    await selectTask(page);
    await page.getByLabel('Rolle', { exact: true }).fill('new definition');
    await expect(page.locator('.djs-element[data-element-id="Flow_2"]')).not.toHaveClass(/bpmn-runtime-selected/);
    await expect(page.locator('.bpmn-runtime')).toContainText('Zuordnung zur aktuellen Editor-Definition nicht bestätigt');
  });
});

test('Resume Retry Cancel preserve Hub bindings, block duplicate clicks and fail closed on stale revision', { timeout: 45000 }, async () => {
  await withEditor(async page => {
    for (const [index, command] of ['resume', 'retry', 'cancel'].entries()) {
      await loadRuntime(page);
      await page.getByRole('button', { name: command[0].toUpperCase() + command.slice(1), exact: true }).click();
      await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toBeDisabled();
      const args = await page.evaluate(index => window.bpmnTest.requests.controls[index].args, index);
      assert.equal(args[0], runtime.workflow_id);
      assert.equal(args[1], command);
      assert.deepEqual(args[2], { expected_revision: runtime.revision, run_id: runtime.run_id,
        plan_hash: runtime.plan_hash, checkpoint_ref: runtime.checkpoint_ref });
      assert.match(args[3], /^bpmn-command-/);
      await respond(page, 'controls', { data: { reason_code: 'stale_workflow_revision' } }, index, true);
      await expect(page.locator('.bpmn-runtime')).toContainText('Status erneut laden');
      await expect(page.locator('.bpmn-runtime dl')).toHaveCount(0);
    }
  });
});
