// Real login, Angular API service, bpmn-js and Chromium against the test Hub.
// Browser fixtures are synthetic; no production credentials or release evidence.
import assert from 'node:assert/strict';
import http from 'node:http';
import { build } from 'esbuild';
import { chromium, expect as playwrightExpect } from '@playwright/test';

const expect = playwrightExpect.configure({ timeout: 15000 });

const hub = 'http://hub:8080';
const bootstrapHeaders = { Authorization: `Bearer ${process.env.BPMN_BROWSER_TOKEN}` };
const bundle = await build({
  absWorkingDir: '/app',
  stdin: {
    contents: `
      import '@angular/compiler';
      import { bootstrapApplication } from '@angular/platform-browser';
      import { provideHttpClient, withInterceptors } from '@angular/common/http';
      import { BpmnBlueprintEditorComponent } from './src/app/features/visual-process/bpmn-blueprint-editor.component';
      import { AgentDirectoryService } from './src/app/services/agent-directory.service';
      import 'bpmn-js/dist/assets/diagram-js.css';
      import 'bpmn-js/dist/assets/bpmn-js.css';
      import 'bpmn-js/dist/assets/bpmn-font/css/bpmn.css';
      document.querySelector('form').addEventListener('submit', async event => {
        event.preventDefault();
        const response = await fetch('/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({username: document.querySelector('#username').value, password: document.querySelector('#password').value}) });
        const result = await response.json();
        if (!response.ok) { document.querySelector('#login-status').textContent = 'Login failed'; return; }
        const token = result.data.access_token;
        document.querySelector('form').remove();
        await bootstrapApplication(BpmnBlueprintEditorComponent, { providers: [
          provideHttpClient(withInterceptors([(request, next) => next(request.clone({setHeaders: {Authorization: 'Bearer ' + token}}))])),
          { provide: AgentDirectoryService, useValue: { list: () => [] } },
        ] });
      });
    `,
    resolveDir: '/app', loader: 'ts',
  },
  bundle: true, write: false, outfile: 'editor.js', platform: 'browser', format: 'iife', target: 'es2022',
  loader: { '.woff2': 'dataurl', '.woff': 'dataurl', '.ttf': 'dataurl', '.eot': 'dataurl', '.svg': 'dataurl' },
  tsconfig: '/app/tsconfig.json',
});
const assets = new Map(bundle.outputFiles.map(file => [file.path.endsWith('.css') ? '/editor.css' : '/editor.js', file.text]));
const server = http.createServer((request, response) => {
  if (request.url === '/login' || request.url.startsWith('/api/visual-process/')) {
    const upstream = http.request(hub + request.url, { method: request.method, headers: request.headers, timeout: 15000 }, result => {
      response.writeHead(result.statusCode, result.headers);
      result.pipe(response);
    });
    upstream.on('timeout', () => upstream.destroy());
    upstream.on('error', () => { if (!response.headersSent) response.writeHead(502); response.end(); });
    request.pipe(upstream);
  } else if (assets.has(request.url)) {
    response.setHeader('Content-Type', request.url.endsWith('.css') ? 'text/css' : 'text/javascript');
    response.end(assets.get(request.url));
  } else if (request.url === '/') {
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    response.end(`<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Isolated BPMN acceptance</title>
      <link rel="stylesheet" href="/editor.css"><style>body{margin:16px;font-family:system-ui}</style></head><body>
      <form><label>Username<input id="username" autocomplete="username"></label>
      <label>Password<input id="password" type="password" autocomplete="current-password"></label>
      <button type="submit">Login</button><span id="login-status"></span></form>
      <app-bpmn-blueprint-editor></app-bpmn-blueprint-editor><script src="/editor.js"></script></body></html>`);
  } else { response.writeHead(404); response.end(); }
});
await new Promise(resolve => server.listen(8080, '127.0.0.1', resolve));
let browser;
let report;
let evidenceAssignment;
let startedAt;
const requests = [];
const errors = [];
try {
  let credentialsResponse;
  const readyDeadline = Date.now() + 150000;
  do {
    credentialsResponse = await fetch(hub + '/test/browser-bootstrap', { headers: bootstrapHeaders, signal: AbortSignal.timeout(15000) });
    if (credentialsResponse.status !== 202) break;
    await new Promise(resolve => setTimeout(resolve, 500));
  } while (Date.now() < readyDeadline);
  assert.equal(credentialsResponse.status, 200);
  const credentials = await credentialsResponse.json();
  evidenceAssignment = credentials.evidence_assignment;
  assert.equal(evidenceAssignment.schema, 'ananta.hub-evidence-assignment.v1');
  assert.equal(evidenceAssignment.evidence_scope, 'test');
  assert.match(evidenceAssignment.run_id, /^RUN_/);
  startedAt = Date.now() / 1000;
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', async response => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith('/api/visual-process/') || path === '/login') {
      const row = { path, status: response.status() };
      requests.push(row);
      if (path.endsWith('/preflight')) row.preflight = await response.json();
      if (response.status() >= 400) {
        const body = await response.json().catch(() => ({}));
        const code = body.reason_code || body.data?.reason_code || body.error;
        if (typeof code === 'string' && /^[a-z_]{1,128}$/.test(code)) row.reason_code = code;
      }
    }
  });
  await page.goto('http://127.0.0.1:8080/');
  await page.getByLabel('Username', { exact: true }).fill(credentials.username);
  await page.getByLabel('Password', { exact: true }).fill(credentials.password);
  await page.getByRole('button', { name: 'Login', exact: true }).click();
  await expect(page.locator('.bpmn-title')).toContainText('Diagramm geladen');
  await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
  const textarea = page.getByLabel('BPMN XML', { exact: true });
  const starter = await textarea.inputValue();
  await textarea.fill(starter.replaceAll('vp_bpmn_blueprint', 'browser_authenticated_workflow')
    .replace('<bpmn:userTask id="Task_2" name="Freigabe" />', '<bpmn:serviceTask id="Task_2" name="Finish" />'));
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await page.locator('.djs-element[data-element-id="Task_1"] .djs-hit').click();
  await page.getByLabel('Name', { exact: true }).fill('Browser delegated task');
  await page.getByRole('button', { name: 'WorkflowRequest', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Start', exact: true }).click();
  await expect(page.getByLabel('Workflow-ID', { exact: true })).not.toHaveValue('');
  const workflowId = await page.getByLabel('Workflow-ID', { exact: true }).inputValue();
  await expect.poll(async () => {
    await page.getByRole('button', { name: 'Status laden', exact: true }).click();
    // Each refresh invalidates the previous request. Wait for the paired
    // status/history read to settle before polling again, otherwise this test
    // continually cancels its own observation of a completed backend run.
    await expect(page.locator('.bpmn-runtime')).not.toContainText('Status wird vom Hub geladen.');
    const status = page.locator('.bpmn-runtime dt:has-text("Status laut Hub") + dd');
    return status.evaluateAll(elements => elements[0]?.textContent || 'snapshot_changed');
  }, { timeout: 60000, intervals: [300, 500, 1000] }).toBe('completed');
  const steps = page.getByRole('table', { name: 'Vom Hub gemeldete Schritte' });
  await expect(steps).toContainText('Task_1');
  await expect(steps).toContainText('Task_2');
  // A full page reload discards every Angular/modeler object. Reauthenticate
  // and recover the original source plus current Hub state by workflow ID.
  await page.reload();
  await page.getByLabel('Username', { exact: true }).fill(credentials.username);
  await page.getByLabel('Password', { exact: true }).fill(credentials.password);
  await page.getByRole('button', { name: 'Login', exact: true }).click();
  await expect(page.locator('.bpmn-title')).toContainText('Diagramm geladen');
  await page.getByLabel('Workflow-ID', { exact: true }).fill(workflowId);
  await page.getByRole('button', { name: 'Lauf wieder laden', exact: true }).click();
  await expect(page.getByLabel('BPMN XML', { exact: true })).toHaveValue(/Browser delegated task/);
  await expect(page.locator('.bpmn-runtime dt:has-text("Status laut Hub") + dd')).toHaveText('completed');
  await expect(page.locator('.bpmn-runtime')).toContainText('Hub-Definition stimmt mit dem geprüften Diagramm überein.');
  await expect(page.getByRole('table', { name: 'Vom Hub gemeldete Schritte' })).toContainText('Task_2');
  assert.equal(requests.filter(row => row.path === '/api/visual-process/workflow/start').length, 1);
  assert.ok(requests.some(row => row.path.endsWith('/bpmn-definition') && row.status === 200));
  // A real timer activation keeps this run controllable without dispatching
  // Worker work or depending on a human approval. Reload also loses all local
  // command preconditions before the persisted paused run is recovered.
  const controlXml = starter.replaceAll('vp_bpmn_blueprint', 'browser_control_workflow')
    .replace('<bpmn:serviceTask id="Task_1" name="Planen" />',
      '<bpmn:intermediateCatchEvent id="Task_1" name="Bounded timer">'
      + '<bpmn:extensionElements><metadata xmlns="https://ananta.local/bpmn">'
      + '{"bpmn_wait":{"timeout_seconds":90}}</metadata></bpmn:extensionElements>'
      + '<bpmn:timerEventDefinition><bpmn:timeDuration>PT60S</bpmn:timeDuration></bpmn:timerEventDefinition>'
      + '</bpmn:intermediateCatchEvent>')
    .replace('<bpmn:userTask id="Task_2" name="Freigabe" />', '<bpmn:serviceTask id="Task_2" name="Finish" />');
  await textarea.fill(controlXml);
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await page.getByLabel('Projekt-ID', { exact: true }).fill('synthetic-browser-controls');
  await page.getByRole('button', { name: 'WorkflowRequest', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeEnabled();
  const controlStart = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/visual-process/workflow/start');
  await page.getByRole('button', { name: 'Start', exact: true }).click();
  assert.equal((await controlStart).status(), 200);
  await expect(page.getByLabel('Workflow-ID', { exact: true })).toHaveValue('browser_control_workflow');
  const controlWorkflowId = await page.getByLabel('Workflow-ID', { exact: true }).inputValue();
  assert.equal(controlWorkflowId, 'browser_control_workflow');
  await expect.poll(async () => {
    await page.getByRole('button', { name: 'Status laden', exact: true }).click();
    await expect(page.locator('.bpmn-runtime')).not.toContainText('Status wird vom Hub geladen.');
    return page.getByRole('table', { name: 'Vom Hub gemeldete Schritte' })
      .evaluateAll(elements => elements.map(element => element.textContent).join(' '));
  }, { timeout: 15000, intervals: [200, 500] }).toContain('waiting');
  await expect(page.getByRole('button', { name: 'Pause', exact: true })).toBeEnabled();
  const pauseResponse = page.waitForResponse(response =>
    new URL(response.url()).pathname.endsWith('/browser_control_workflow/pause'));
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  const pausedResponse = await pauseResponse;
  assert.equal(pausedResponse.status(), 200, JSON.stringify(await pausedResponse.json()));
  await expect(page.locator('.bpmn-runtime dt:has-text("Status laut Hub") + dd')).toHaveText('paused');
  await page.reload();
  await page.getByLabel('Username', { exact: true }).fill(credentials.username);
  await page.getByLabel('Password', { exact: true }).fill(credentials.password);
  await page.getByRole('button', { name: 'Login', exact: true }).click();
  await expect(page.locator('.bpmn-title')).toContainText('Diagramm geladen');
  await page.getByLabel('Workflow-ID', { exact: true }).fill(controlWorkflowId);
  await page.getByRole('button', { name: 'Lauf wieder laden', exact: true }).click();
  await expect(page.locator('.bpmn-runtime dt:has-text("Status laut Hub") + dd')).toHaveText('paused');
  await expect(page.getByRole('button', { name: 'Pause', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Resume', exact: true }).click();
  await expect(page.locator('.bpmn-runtime dt:has-text("Status laut Hub") + dd')).toHaveText('running');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.locator('.bpmn-runtime dt:has-text("Status laut Hub") + dd')).toHaveText('cancelled');
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Status laden', exact: true }).click();
  await expect.poll(() => page.locator('.bpmn-canvas .bpmn-runtime-cancelled[data-element-id^="Flow_"]')
    .evaluateAll(elements => [...new Set(elements.map(element => element.getAttribute('data-element-id')))].sort()))
    .toEqual(['Flow_1', 'Flow_2', 'Flow_3']);
  await expect(page.getByRole('table', { name: 'Vom Hub gemeldete Schritte' })).toContainText('cancelled');
  for (const command of ['pause', 'resume', 'cancel']) {
    assert.equal(requests.filter(row => row.path.endsWith('/' + command) && row.status === 200).length, 1);
  }
  assert.equal(requests.filter(row => row.path === '/api/visual-process/workflow/start').length, 2);
  // Retry an approval run after an actual reload. The Hub must reopen the
  // pending gate, not grant approval or treat prior cancellation as completion.
  const retryXml = starter.replaceAll('vp_bpmn_blueprint', 'browser_retry_workflow')
    .replace('<bpmn:serviceTask id="Task_1" name="Planen" />', '<bpmn:userTask id="Task_1" name="Review" />');
  await page.getByLabel('BPMN XML', { exact: true }).fill(retryXml);
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await page.getByRole('button', { name: 'WorkflowRequest', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeEnabled();
  const retryStart = page.waitForResponse(response => new URL(response.url()).pathname === '/api/visual-process/workflow/start');
  await page.getByRole('button', { name: 'Start', exact: true }).click();
  assert.equal((await retryStart).status(), 200);
  const retryWorkflowId = 'browser_retry_workflow';
  await expect.poll(async () => {
    await page.getByRole('button', { name: 'Status laden', exact: true }).click();
    await expect(page.locator('.bpmn-runtime')).not.toContainText('Status wird vom Hub geladen.');
    return page.getByRole('table', { name: 'Vom Hub gemeldete Schritte' })
      .evaluateAll(elements => elements.map(element => element.textContent).join(' '));
  }, { timeout: 15000, intervals: [200, 500] }).toContain('waiting');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.locator('.bpmn-runtime dt:has-text("Status laut Hub") + dd')).toHaveText('cancelled');
  await page.reload();
  await page.getByLabel('Username', { exact: true }).fill(credentials.username);
  await page.getByLabel('Password', { exact: true }).fill(credentials.password);
  await page.getByRole('button', { name: 'Login', exact: true }).click();
  await expect(page.locator('.bpmn-title')).toContainText('Diagramm geladen');
  await page.getByLabel('Workflow-ID', { exact: true }).fill(retryWorkflowId);
  await page.getByRole('button', { name: 'Lauf wieder laden', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByRole('table', { name: 'Vom Hub gemeldete Schritte' })).toContainText('waiting');
  await expect.poll(() => page.locator('.bpmn-canvas .bpmn-runtime-cancelled').count()).toBe(0);
  await expect(page.locator('.bpmn-runtime')).toContainText('pending');
  await page.reload();
  await page.getByLabel('Username', { exact: true }).fill(credentials.username);
  await page.getByLabel('Password', { exact: true }).fill(credentials.password);
  await page.getByRole('button', { name: 'Login', exact: true }).click();
  await expect(page.locator('.bpmn-title')).toContainText('Diagramm geladen');
  await page.getByLabel('Workflow-ID', { exact: true }).fill(retryWorkflowId);
  await page.getByRole('button', { name: 'Lauf wieder laden', exact: true }).click();
  await expect(page.getByRole('table', { name: 'Vom Hub gemeldete Schritte' })).toContainText('waiting');
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.locator('.bpmn-runtime dt:has-text("Status laut Hub") + dd')).toHaveText('cancelled');
  assert.equal(requests.filter(row => row.path.endsWith('/browser_retry_workflow/retry') && row.status === 200).length, 1);
  assert.equal(requests.filter(row => row.path === '/api/visual-process/workflow/start').length, 3);
  assert.deepEqual(errors, []);
  for (const path of ['/login', '/api/visual-process/bpmn/import', '/api/visual-process/workflow-request', '/api/visual-process/workflow/start']) {
    assert.ok(requests.some(row => row.path === path && row.status === 200), path);
  }
  report = { passed: true, workflow_id: workflowId, requests, login: 'production_login_route', api_doubles: false,
    authenticated_page_reload: true, persisted_definition_recovered: true, duplicate_start_requests: false,
    control_workflow_id: controlWorkflowId, paused_page_reload: true, pause_resume_cancel: true,
    cancelled_timer_retry_denied: true, approval_retry_after_reload: true,
    reopened_approval_page_reload: true };
} catch (error) {
  report = { passed: false, error: String(error), requests, page_errors: errors };
} finally {
  await browser?.close();
}
report = { ...report, evidence_assignment: evidenceAssignment, started_at: startedAt, finished_at: Date.now() / 1000 };
const accepted = await fetch(hub + '/test/browser-report', {
  method: 'POST', headers: { ...bootstrapHeaders, 'Content-Type': 'application/json' },
  body: JSON.stringify(report), signal: AbortSignal.timeout(15000),
});
assert.equal(accepted.status, 200);
console.log(report.passed ? 'PASS authenticated Chromium editor' : `FAIL authenticated Chromium editor: ${report.error}`);
// Keep this isolated service alive until the Hub collects its report and exits.
