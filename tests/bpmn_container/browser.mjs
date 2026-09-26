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
    return await status.count() ? status.innerText() : 'snapshot_changed';
  }, { timeout: 60000, intervals: [300, 500, 1000] }).toBe('completed');
  const steps = page.getByRole('table', { name: 'Vom Hub gemeldete Schritte' });
  await expect(steps).toContainText('Task_1');
  await expect(steps).toContainText('Task_2');
  assert.deepEqual(errors, []);
  for (const path of ['/login', '/api/visual-process/bpmn/import', '/api/visual-process/workflow-request', '/api/visual-process/workflow/start']) {
    assert.ok(requests.some(row => row.path === path && row.status === 200), path);
  }
  report = { passed: true, workflow_id: workflowId, requests, login: 'production_login_route', api_doubles: false };
} catch (error) {
  report = { passed: false, error: String(error), requests, page_errors: errors };
} finally {
  await browser?.close();
}
const accepted = await fetch(hub + '/test/browser-report', {
  method: 'POST', headers: { ...bootstrapHeaders, 'Content-Type': 'application/json' },
  body: JSON.stringify(report), signal: AbortSignal.timeout(15000),
});
assert.equal(accepted.status, 200);
console.log(report.passed ? 'PASS authenticated Chromium editor' : `FAIL authenticated Chromium editor: ${report.error}`);
// Keep this isolated service alive until the Hub collects its report and exits.
