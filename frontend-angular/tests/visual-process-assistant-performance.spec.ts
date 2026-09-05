import { expect, test, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

const FRONTEND_SOURCE_PATHS = [
  'frontend-angular/package.json',
  'frontend-angular/playwright.vpa-performance.config.ts',
  'frontend-angular/src/app/features/visual-process/visual-process-api.service.ts',
  'frontend-angular/src/app/features/visual-process/visual-process-canvas.component.ts',
  'frontend-angular/src/app/features/visual-process/visual-process-editor.component.html',
  'frontend-angular/src/app/features/visual-process/visual-process-editor.component.ts',
  'frontend-angular/src/app/features/visual-process/vp-assistant-api.service.ts',
  'frontend-angular/src/app/features/visual-process/vp-assistant-bridge.service.ts',
  'frontend-angular/src/app/features/visual-process/vp-assistant-bubble.component.ts',
  'frontend-angular/src/app/features/visual-process/vp-assistant-context.service.ts',
  'frontend-angular/src/app/features/visual-process/vp-canvas-interaction.service.ts',
  'frontend-angular/src/app/features/visual-process/vp-editor-config.ts',
  'frontend-angular/src/app/features/visual-process/vp-editor-state.facade.ts',
  'frontend-angular/src/app/features/visual-process/vp-node-palette.component.ts',
  'frontend-angular/tests/visual-process-assistant-performance.spec.ts',
] as const;

const REPOSITORY_ROOT = path.resolve(process.cwd(), '..');
const EVIDENCE_OUTPUT = path.join(
  REPOSITORY_ROOT,
  'artifacts/test-gates/visual-process-assistant-frontend-performance-evidence.json',
);
const LOCAL_TEST_TOKEN = [
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9',
  'eyJzdWIiOiJ2cGEtZTJlLXVzZXIiLCJyb2xlIjoiYWRtaW4iLCJ1c2VybmFtZSI6InZwYS1lMmUifQ',
  'dGVzdC1vbmx5LXNpZ25hdHVyZQ',
].join('.');

type FocusProbe = {
  activeHoverTimers: Set<number>;
  maxHoverTimers: number;
};

declare global {
  interface Window {
    __vpaFocusProbe?: FocusProbe;
  }
}

function percentile(values: readonly number[], percentileValue: number): number {
  const sorted = [...values].sort((left, right) => left - right);
  if (!sorted.length) throw new Error('visual_process_frontend_performance_samples_required');
  const rank = Math.max(1, Math.ceil(sorted.length * percentileValue / 100));
  return sorted[Math.min(rank, sorted.length) - 1];
}

function rounded(value: number): number {
  return Number(value.toFixed(6));
}

function sourceHashes(): Record<string, string> {
  return Object.fromEntries(FRONTEND_SOURCE_PATHS.map(relative => [
    relative,
    createHash('sha256').update(fs.readFileSync(path.join(REPOSITORY_ROOT, relative))).digest('hex'),
  ]));
}

async function installLocalTestIdentity(page: Page): Promise<void> {
  await page.addInitScript(token => {
    localStorage.setItem('ananta.user.token', token);
    localStorage.setItem('ananta.shell.mode', 'advanced');
  }, LOCAL_TEST_TOKEN);
}

test.describe.configure({ retries: 0, mode: 'serial' });

test('1000 focus transitions remain heap- and subscription-bounded', async ({ page, context, browser }) => {
  await page.addInitScript(() => {
    const originalSetTimeout = window.setTimeout.bind(window);
    const originalClearTimeout = window.clearTimeout.bind(window);
    const probe: FocusProbe = { activeHoverTimers: new Set<number>(), maxHoverTimers: 0 };
    window.__vpaFocusProbe = probe;
    window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) => {
      let timer = 0;
      const wrapped = (...callbackArgs: unknown[]) => {
        if (delay === 350) probe.activeHoverTimers.delete(timer);
        if (typeof handler === 'function') handler(...callbackArgs);
        else window.eval(String(handler));
      };
      timer = originalSetTimeout(wrapped, delay, ...args);
      if (delay === 350) {
        probe.activeHoverTimers.add(timer);
        probe.maxHoverTimers = Math.max(probe.maxHoverTimers, probe.activeHoverTimers.size);
      }
      return timer;
    }) as typeof window.setTimeout;
    window.clearTimeout = ((timer?: number) => {
      if (typeof timer === 'number') probe.activeHoverTimers.delete(timer);
      originalClearTimeout(timer);
    }) as typeof window.clearTimeout;
  });

  let activeAssistantRequests = 0;
  let maxAssistantRequests = 0;
  const requestIds = new Set<object>();
  const isAssistantRequest = (url: string) => url.includes('/api/visual-process/assistant/v1/');
  page.on('request', current => {
    if (!isAssistantRequest(current.url())) return;
    requestIds.add(current);
    activeAssistantRequests += 1;
    maxAssistantRequests = Math.max(maxAssistantRequests, activeAssistantRequests);
  });
  const releaseRequest = (current: object) => {
    if (!requestIds.delete(current)) return;
    activeAssistantRequests = Math.max(0, activeAssistantRequests - 1);
  };
  page.on('requestfinished', releaseRequest);
  page.on('requestfailed', releaseRequest);

  await page.route('**/config/model-routing/profiles', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ profiles: [], fallback_groups: {}, status: 'ready' }),
  }));
  await page.route('**/api/ml-intern-training/**', route => {
    const pathname = new URL(route.request().url()).pathname;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(pathname.endsWith('/capabilities')
        ? { gpu_profiles: [], base_models: [] }
        : { items: [], next_cursor: null, total: 0 }),
    });
  });
  await page.route('**/api/visual-process/assistant/v1/**', async route => {
    const requestUrl = new URL(route.request().url());
    const pathname = requestUrl.pathname;
    const method = route.request().method();
    await new Promise(resolve => setTimeout(resolve, 10));
    if (method === 'GET' && pathname.endsWith('/capabilities')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          contract_version: 'ananta.visual_process.assistant.capabilities.v1',
          registry_inspector: true,
          hover_help: true,
          assistant_chat: true,
          ai_patches: true,
          limits: {},
        }),
      });
      return;
    }
    if (method === 'POST' && pathname.endsWith('/contexts')) {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          context_id: `ctx-sha256:${'0'.repeat(64)}`,
          graph_id: body['graph_id'],
          definition_revision: 0,
          definition_hash: body['draft_graph'] && typeof body['draft_graph'] === 'object'
            ? String((body['draft_graph'] as Record<string, unknown>)['base_graph_hash'] ?? '0'.repeat(64))
            : '0'.repeat(64),
          editor_mode: body['editor_mode'],
          locale: body['locale'] ?? 'de',
          context: {
            contract_version: 'ananta.visual_process.editor_context.v1',
            graph_id: body['graph_id'],
            repository_revision: body['repository_revision'],
            codecompass_manifest_hash: body['codecompass_manifest_hash'],
            source_allowlist_version: body['source_allowlist_version'],
            prompt_version: 'visual-process-assistant.v1',
            graph_schema_version: '1',
            node_registry_version: '1.0.0',
            definition_revision: 0,
            definition_hash: '0'.repeat(64),
            draft_hash: '0'.repeat(64),
            editor_mode: body['editor_mode'],
            locale: body['locale'] ?? 'de',
            location: body['location'],
            graph_excerpt: {},
            effective_configuration: {},
            validation_issues: [],
            evidence_refs: [],
            allowed_mutations: [],
            extensions: {},
          },
          created_at: 0,
        }),
      });
      return;
    }
    if (method === 'POST' && pathname.endsWith('/conversations')) {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          conversation_id: 'conversation-performance-fixture',
          graph_id: 'unsaved-graph',
          status: 'active',
          active_context_id: `ctx-sha256:${'0'.repeat(64)}`,
          created_at: 0,
          updated_at: 0,
          requests: [],
        }),
      });
      return;
    }
    if (method === 'POST' && pathname.includes('/questions')) {
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          request_id: 'request-performance-fixture',
          conversation_id: 'conversation-performance-fixture',
          context_id: `ctx-sha256:${'0'.repeat(64)}`,
          prompt_context_id: `ctx-sha256:${'0'.repeat(64)}`,
          prompt_version: 'visual-process-assistant.v1',
          client_request_id: 'client-performance-fixture',
          status: 'completed',
          response: {
            summary: 'Performance fixture completed.',
            location: { target_kind: 'node', graph_id: 'unsaved-graph' },
            explanation: 'The request subscription completed deterministically.',
            options: [],
            warnings: [],
            next_actions: [],
            evidence: [],
            context_id: `ctx-sha256:${'0'.repeat(64)}`,
          },
          created_at: 0,
          updated_at: 0,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
  });

  await installLocalTestIdentity(page);
  await page.goto('/process-designer', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.vpe-canvas-wrap')).toBeVisible();
  await page.getByRole('button', { name: '+ Schritt' }).click();
  const paletteItem = page.locator('.vp-palette-item:not([disabled])').first();
  await expect(paletteItem).toBeVisible();
  await paletteItem.click();
  const node = page.locator('.vpe-node-g').first();
  const canvas = page.locator('.vpe-canvas-wrap');
  await expect(node).toBeVisible();

  const cdp = await context.newCDPSession(page);
  await cdp.send('HeapProfiler.enable');
  const collectHeap = async () => {
    await cdp.send('HeapProfiler.collectGarbage');
    await page.waitForTimeout(100);
    const usage = await cdp.send('Runtime.getHeapUsage');
    return Number(usage.usedSize);
  };

  await page.evaluate(() => {
    const targets = [
      document.querySelector<HTMLElement>('.vpe-node-g'),
      document.querySelector<HTMLElement>('.vpe-canvas-wrap'),
    ];
    for (let index = 0; index < 100; index += 1) targets[index % 2]?.focus();
  });
  await page.waitForTimeout(450);
  await page.evaluate(() => {
    const probe = window.__vpaFocusProbe;
    if (!probe) throw new Error('visual_process_focus_probe_missing');
    probe.activeHoverTimers.clear();
    probe.maxHoverTimers = 0;
  });
  const heapBefore = await collectHeap();
  const durations = await page.evaluate(() => {
    const targets = [
      document.querySelector<HTMLElement>('.vpe-node-g'),
      document.querySelector<HTMLElement>('.vpe-canvas-wrap'),
    ];
    if (targets.some(target => !target)) throw new Error('visual_process_focus_targets_missing');
    const samples: number[] = [];
    for (let index = 0; index < 1_000; index += 1) {
      const started = performance.now();
      targets[index % 2]!.focus();
      samples.push(performance.now() - started);
    }
    return samples;
  });
  await page.waitForTimeout(450);
  const heapAfter = await collectHeap();

  await node.focus();
  await node.press('Enter');
  const question = page.getByLabel('Frage zu diesem Kontext');
  await expect(question).toBeVisible({ timeout: 10_000 });
  await question.fill('Subscription-Gate prüfen');
  await page.getByRole('button', { name: 'Fragen' }).click();
  await expect(page.locator('.request-status')).toContainText('Antwort vollständig');
  await expect.poll(() => activeAssistantRequests).toBe(0);

  const timerCounts = await page.evaluate(() => ({
    active: window.__vpaFocusProbe?.activeHoverTimers.size ?? -1,
    maximum: window.__vpaFocusProbe?.maxHoverTimers ?? -1,
  }));
  const heapGrowthMiB = Math.max(0, heapAfter - heapBefore) / (1024 * 1024);
  const measurements = {
    focus_transitions: 1_000,
    warmup_iterations: 100,
    p50_ms: rounded(percentile(durations, 50)),
    p95_ms: rounded(percentile(durations, 95)),
    heap_before_mib: rounded(heapBefore / (1024 * 1024)),
    heap_after_mib: rounded(heapAfter / (1024 * 1024)),
    heap_growth_mib: rounded(heapGrowthMiB),
    hover_subscriptions_per_editor: timerCounts.maximum,
    active_hover_timers_after_stabilization: timerCounts.active,
    conversation_subscriptions_per_editor: maxAssistantRequests,
    active_conversation_requests_after_completion: activeAssistantRequests,
    editor_instances: 1,
  };

  expect(measurements.heap_growth_mib).toBeLessThanOrEqual(20);
  expect(measurements.hover_subscriptions_per_editor).toBeLessThanOrEqual(1);
  expect(measurements.active_hover_timers_after_stabilization).toBe(0);
  expect(measurements.conversation_subscriptions_per_editor).toBeLessThanOrEqual(1);
  expect(measurements.active_conversation_requests_after_completion).toBe(0);

  const packageJson = JSON.parse(
    fs.readFileSync(path.join(REPOSITORY_ROOT, 'frontend-angular/package.json'), 'utf-8'),
  ) as { version: string; dependencies: Record<string, string> };
  const payload = {
    schema: 'ananta.visual-process-assistant-frontend-performance-evidence.v1',
    source_hashes: sourceHashes(),
    environment: {
      browser: `Chromium ${browser.version()}`,
      build: `ananta-angular-${packageJson.version}-angular-${packageJson.dependencies['@angular/core']}`,
      hardware_class: `local-cpu-${os.cpus().length}-logical-memory-${Math.round(os.totalmem() / (1024 ** 3))}-gib`,
      warmup_iterations: 100,
      repetitions: 1_000,
    },
    measurements,
    evidence_paths: ['frontend-angular/tests/visual-process-assistant-performance.spec.ts'],
  };
  fs.mkdirSync(path.dirname(EVIDENCE_OUTPUT), { recursive: true });
  fs.writeFileSync(EVIDENCE_OUTPUT, `${JSON.stringify(payload, null, 2)}\n`, 'utf-8');
});
