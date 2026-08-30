import { CDPSession, Page, Route, expect, test } from '@playwright/test';

const EVIDENCE_CLASSIFICATION = 'local_diagnostic_not_release_evidence' as const;
const CARD_COUNT = 1_000;
const PAGE_SIZE = 100;
const PAGE_COUNT = CARD_COUNT / PAGE_SIZE;
const VIEW_GROUP_COUNT = 10;
const SAMPLE_COUNT = 3;
const MEBIBYTE = 1024 * 1024;
const PROJECT_ID = 'kanban-performance-project';

const STATUS_GROUPS = [
  { status: 'todo', columnId: 'todo' },
  { status: 'pending', columnId: 'todo' },
  { status: 'ready', columnId: 'todo' },
  { status: 'in_progress', columnId: 'in_progress' },
  { status: 'running', columnId: 'in_progress' },
  { status: 'delegated', columnId: 'in_progress' },
  { status: 'blocked_by_dependency', columnId: 'blocked' },
  { status: 'failed', columnId: 'blocked' },
  { status: 'completed', columnId: 'completed' },
  { status: 'cancelled', columnId: 'completed' },
] as const;

const VIEWPORTS = [
  {
    name: 'desktop',
    width: 1600,
    height: 900,
    budgets: {
      initialRenderP95Ms: 4_000,
      filterP95Ms: 1_500,
      longTaskTotalP95Ms: 2_500,
      longestTaskP95Ms: 750,
      retainedHeapDeltaBytes: 96 * MEBIBYTE,
      retainedHeapBytes: 192 * MEBIBYTE,
    },
  },
  {
    name: 'mobile',
    width: 390,
    height: 844,
    budgets: {
      initialRenderP95Ms: 5_000,
      filterP95Ms: 1_800,
      longTaskTotalP95Ms: 3_000,
      longestTaskP95Ms: 900,
      retainedHeapDeltaBytes: 112 * MEBIBYTE,
      retainedHeapBytes: 224 * MEBIBYTE,
    },
  },
] as const;

type DiagnosticState = {
  startedAt: number;
  longTaskSupported: boolean;
  longTasks: number[];
  observer?: PerformanceObserver;
};

type Sample = {
  initialRenderMs: number;
  filterMs: number;
  longTaskCount: number;
  longTaskTotalMs: number;
  longestTaskMs: number;
  retainedHeapBeforeBytes: number | null;
  retainedHeapAfterBytes: number | null;
  retainedHeapDeltaBytes: number | null;
};

declare global {
  interface Window {
    __anantaKanbanDiagnostic?: DiagnosticState;
  }
}

const cards = Array.from({ length: CARD_COUNT }, (_, index) => {
  const group = STATUS_GROUPS[index % STATUS_GROUPS.length];
  const id = `perf-card-${String(index).padStart(4, '0')}`;
  return {
    schema_version: 'kanban.v1',
    id,
    board_id: 'hub',
    title: `Performance Card ${String(index).padStart(4, '0')}`,
    description: index === 420
      ? 'Deterministic filter marker needle-0420'
      : `Deterministic diagnostic card ${index}`,
    status: group.status,
    column_id: group.columnId,
    position: index,
    revision: 1,
    priority: index % 3 === 0 ? 'High' : index % 3 === 1 ? 'Medium' : 'Low',
    assignee: null,
    labels: [`status-group-${index % STATUS_GROUPS.length}`],
    blocked: group.columnId === 'blocked',
    dependencies: [],
    comment_count: 0,
    activity_count: 1,
    created_at: '2026-07-23T10:00:00Z',
    updated_at: '2026-07-23T10:00:00Z',
  };
});

const columns = [
  {
    id: 'todo',
    title: 'To do',
    statuses: ['todo', 'pending', 'ready'],
    card_count: 300,
  },
  {
    id: 'in_progress',
    title: 'In progress',
    statuses: ['in_progress', 'running', 'delegated'],
    card_count: 300,
  },
  {
    id: 'blocked',
    title: 'Blocked',
    statuses: ['blocked_by_dependency', 'failed'],
    card_count: 200,
  },
  {
    id: 'completed',
    title: 'Completed',
    statuses: ['completed', 'cancelled'],
    card_count: 200,
  },
];

const boards = Array.from({ length: VIEW_GROUP_COUNT }, (_, index) => ({
  id: index === 0 ? 'hub' : `diagnostic-view-${index}`,
  name: `Diagnostic view ${index + 1}`,
  scope_type: index === 0 ? 'hub' : 'worker',
  scope_id: index === 0 ? null : `diagnostic-worker-${index}`,
  revision: 'perf-snapshot-1',
  card_count: CARD_COUNT,
  capabilities: ['kanban.read'],
}));

function matchingCards(query: string): typeof cards {
  const normalized = query.trim().toLowerCase();
  return normalized
    ? cards.filter(card =>
      card.title.toLowerCase().includes(normalized)
      || card.description.toLowerCase().includes(normalized))
    : cards;
}

async function json(route: Route, data: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(status >= 400 ? data : { status: 'success', data }),
  });
}

async function installFixtures(page: Page): Promise<{ cardPageRequests: () => number }> {
  const token = `e30.${Buffer.from(JSON.stringify({
    sub: 'kanban-performance-diagnostic',
    role: 'admin',
    exp: 4_102_444_800,
  })).toString('base64url')}.sig`;
  await page.addInitScript(({ authToken }) => {
    localStorage.setItem('ananta.user.token', authToken);
    localStorage.setItem('ananta.shell.mode', 'advanced');
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', role: 'hub', url: 'http://127.0.0.1:5000', token: '' },
    ]));
    const state: DiagnosticState = {
      startedAt: performance.now(),
      longTaskSupported: false,
      longTasks: [],
    };
    try {
      state.observer = new PerformanceObserver(list => {
        for (const entry of list.getEntries()) state.longTasks.push(entry.duration);
      });
      state.observer.observe({ type: 'longtask', buffered: true });
      state.longTaskSupported = true;
    } catch {
      state.longTaskSupported = false;
    }
    window.__anantaKanbanDiagnostic = state;
  }, { authToken: token });

  let cardPageRequests = 0;
  await page.route('**://127.0.0.1:5000/**', route => json(route, {}));
  await page.route('**/me', route => json(route, {
    sub: 'kanban-performance-diagnostic',
    username: 'diagnostic',
    role: 'admin',
    capabilities: [],
  }));
  await page.route('**/api/projects', route => json(route, {
    items: [{
      id: PROJECT_ID,
      name: 'Kanban performance project',
      description: 'Deterministic local performance fixture',
      status: 'active',
      is_active: true,
      origin: 'native',
      team_id: null,
      version: 1,
      created_at: 1,
      updated_at: 1,
      archived_at: null,
    }],
    count: 1,
  }));
  await page.route('**/config/features/v1', route => json(route, {
    schema: 'ananta.dashboard-feature-flags.v1',
    features: {
      angular_kanban: true,
      angular_model_dashboard: false,
      tui_kanban: false,
      tui_model_menu: false,
    },
  }));
  await page.route('**/api/v1/kanban/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith('/boards') && request.method() === 'GET') {
      return json(route, { items: boards, next_cursor: null });
    }
    if (path.endsWith('/boards/hub/snapshot') && request.method() === 'GET') {
      return json(route, {
        schema_version: 'kanban.snapshot.v1',
        board: {
          schema_version: 'kanban.v1',
          ...boards[0],
          columns,
        },
        cards,
        event_sequence: 0,
      });
    }
    if (path.endsWith('/boards/hub/events') && request.method() === 'GET') {
      const rawSequence = url.searchParams.get('after_sequence')
        ?? request.headers()['last-event-id']
        ?? '0';
      const afterSequence = Number.parseInt(rawSequence, 10);
      const sequence = Number.isSafeInteger(afterSequence) && afterSequence >= 0
        ? afterSequence
        : 0;
      return json(route, {
        schema_version: 'kanban.event-batch.v1',
        board_id: 'hub',
        requested_after_sequence: sequence,
        events: [],
        overflow: false,
        gap_detected: false,
        gap_reason: null,
        overflow_reason: null,
        snapshot_required: false,
        snapshot_url: '/api/v1/kanban/boards/hub/snapshot',
        next_after_sequence: sequence,
        latest_sequence: sequence,
        has_more: false,
      });
    }
    if (path.endsWith('/cards') && request.method() === 'GET') {
      cardPageRequests += 1;
      const query = (url.searchParams.get('q') ?? '').trim().toLowerCase();
      if (query) {
        return json(route, {
          board_id: 'hub',
          board_revision: 'perf-snapshot-1',
          items: matchingCards(query).slice(0, PAGE_SIZE),
          next_cursor: null,
        });
      }
      const rawCursor = url.searchParams.get('cursor');
      const pageIndex = rawCursor?.startsWith('perf-page-')
        ? Number(rawCursor.slice('perf-page-'.length))
        : 0;
      const start = pageIndex * PAGE_SIZE;
      return json(route, {
        board_id: 'hub',
        board_revision: 'perf-snapshot-1',
        items: cards.slice(start, start + PAGE_SIZE),
        next_cursor: pageIndex + 1 < PAGE_COUNT ? `perf-page-${pageIndex + 1}` : null,
      });
    }
    if (/\/boards\/[^/]+$/.test(path) && request.method() === 'GET') {
      const boardId = decodeURIComponent(path.split('/').at(-1) ?? 'hub');
      const board = boards.find(item => item.id === boardId) ?? boards[0];
      return json(route, { ...board, columns });
    }
    return json(route, {});
  });
  return { cardPageRequests: () => cardPageRequests };
}

async function settleFrames(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>(resolve => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
}

async function heapBytes(page: Page, cdp: CDPSession | null): Promise<number | null> {
  if (cdp) {
    await cdp.send('HeapProfiler.collectGarbage');
    const usage = await cdp.send('Runtime.getHeapUsage') as { usedSize?: number };
    return typeof usage.usedSize === 'number' ? usage.usedSize : null;
  }
  return page.evaluate(() => {
    const memory = (performance as Performance & {
      memory?: { usedJSHeapSize?: number };
    }).memory;
    return typeof memory?.usedJSHeapSize === 'number' ? memory.usedJSHeapSize : null;
  });
}

function percentile(values: number[], quantile: number): number {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.max(0, Math.ceil(sorted.length * quantile) - 1)] ?? 0;
}

test('local diagnostic Kanban performance with 1000 paginated cards and 10 views', async ({
  browserName,
  page,
}, testInfo) => {
  const fixture = await installFixtures(page);
  let cdp: CDPSession | null = null;
  if (browserName === 'chromium') {
    cdp = await page.context().newCDPSession(page);
    await cdp.send('HeapProfiler.enable');
  }

  const viewportResults = [];
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });

    await page.goto(
      `/board?projectId=${PROJECT_ID}&diagnostic=${viewport.name}-warmup`,
      { waitUntil: 'domcontentloaded' },
    );
    await expect(page.getByTestId('kanban-board')).toBeVisible();
    await expect(page.locator('.kanban-card')).toHaveCount(CARD_COUNT);
    await page.getByRole('searchbox', { name: 'Suche' }).fill('needle-0420');
    await expect(page.locator('.kanban-card')).toHaveCount(1);

    const samples: Sample[] = [];
    for (let sampleIndex = 0; sampleIndex < SAMPLE_COUNT; sampleIndex += 1) {
      const heapBefore = await heapBytes(page, cdp);
      await page.goto(
        `/board?projectId=${PROJECT_ID}&diagnostic=${viewport.name}-${sampleIndex}`,
        { waitUntil: 'domcontentloaded' },
      );
      await expect(page.getByTestId('kanban-board')).toBeVisible();
      await expect(page.locator('.kanban-card')).toHaveCount(CARD_COUNT);
      await settleFrames(page);
      const initial = await page.evaluate(() => {
        const state = window.__anantaKanbanDiagnostic;
        return {
          elapsedMs: performance.now() - (state?.startedAt ?? 0),
          longTasks: [...(state?.longTasks ?? [])],
          longTaskSupported: state?.longTaskSupported ?? false,
        };
      });

      const filterStartedAt = await page.evaluate(() => performance.now());
      await page.getByRole('searchbox', { name: 'Suche' }).fill('needle-0420');
      await expect(page.locator('.kanban-card')).toHaveCount(1);
      await settleFrames(page);
      const filtered = await page.evaluate(({ startedAt, initialLongTaskCount }) => {
        const state = window.__anantaKanbanDiagnostic;
        return {
          elapsedMs: performance.now() - startedAt,
          longTasks: [...(state?.longTasks ?? [])].slice(initialLongTaskCount),
        };
      }, {
        startedAt: filterStartedAt,
        initialLongTaskCount: initial.longTasks.length,
      });
      const heapAfter = await heapBytes(page, cdp);
      const longTasks = [...initial.longTasks, ...filtered.longTasks];
      samples.push({
        initialRenderMs: initial.elapsedMs,
        filterMs: filtered.elapsedMs,
        longTaskCount: longTasks.length,
        longTaskTotalMs: longTasks.reduce((total, duration) => total + duration, 0),
        longestTaskMs: Math.max(0, ...longTasks),
        retainedHeapBeforeBytes: heapBefore,
        retainedHeapAfterBytes: heapAfter,
        retainedHeapDeltaBytes: heapBefore === null || heapAfter === null
          ? null
          : Math.max(0, heapAfter - heapBefore),
      });
    }

    const heapDeltas = samples
      .map(sample => sample.retainedHeapDeltaBytes)
      .filter((value): value is number => value !== null);
    const heapAfterValues = samples
      .map(sample => sample.retainedHeapAfterBytes)
      .filter((value): value is number => value !== null);
    const summary = {
      initialRenderP50Ms: percentile(samples.map(sample => sample.initialRenderMs), 0.5),
      initialRenderP95Ms: percentile(samples.map(sample => sample.initialRenderMs), 0.95),
      filterP50Ms: percentile(samples.map(sample => sample.filterMs), 0.5),
      filterP95Ms: percentile(samples.map(sample => sample.filterMs), 0.95),
      longTaskTotalP95Ms: percentile(samples.map(sample => sample.longTaskTotalMs), 0.95),
      longestTaskP95Ms: percentile(samples.map(sample => sample.longestTaskMs), 0.95),
      retainedHeapDeltaP95Bytes: heapDeltas.length ? percentile(heapDeltas, 0.95) : null,
      retainedHeapP95Bytes: heapAfterValues.length ? percentile(heapAfterValues, 0.95) : null,
      longTaskApiAvailable: samples.some(sample => sample.longTaskCount > 0)
        || await page.evaluate(() => window.__anantaKanbanDiagnostic?.longTaskSupported ?? false),
      jsHeapAvailable: heapAfterValues.length === samples.length,
    };
    viewportResults.push({
      viewport: { name: viewport.name, width: viewport.width, height: viewport.height },
      budgets: viewport.budgets,
      samples,
      summary,
    });
  }

  const report = {
    schema: 'ananta.kanban-performance-local-diagnostic.v1',
    evidence_classification: EVIDENCE_CLASSIFICATION,
    formal: false,
    release_evidence: false,
    dataset: {
      cards: CARD_COUNT,
      cursor_pages: PAGE_COUNT,
      page_size: PAGE_SIZE,
      view_groups: VIEW_GROUP_COUNT,
      status_groups: STATUS_GROUPS.length,
      canonical_columns: columns.length,
    },
    methodology: {
      warmup_runs_per_viewport: 1,
      measured_runs_per_viewport: SAMPLE_COUNT,
      initial_render_end: '1000 .kanban-card nodes plus two animation frames',
      filter_end: 'one matching .kanban-card node plus two animation frames',
      heap: browserName === 'chromium'
        ? 'Chromium CDP Runtime.getHeapUsage after HeapProfiler.collectGarbage'
        : 'performance.memory when available',
      long_tasks: 'PerformanceObserver longtask entries when available',
    },
    card_page_requests: fixture.cardPageRequests(),
    viewports: viewportResults,
  };
  await testInfo.attach('kanban-local-diagnostic.json', {
    body: Buffer.from(JSON.stringify(report, null, 2)),
    contentType: 'application/json',
  });
  console.log(`KANBAN_LOCAL_DIAGNOSTIC ${JSON.stringify(report)}`);

  expect(report.evidence_classification).toBe(EVIDENCE_CLASSIFICATION);
  expect(report.formal).toBe(false);
  for (const result of viewportResults) {
    expect(
      result.summary.initialRenderP95Ms,
      `${result.viewport.name} initial render p95`,
    ).toBeLessThanOrEqual(result.budgets.initialRenderP95Ms);
    expect(
      result.summary.filterP95Ms,
      `${result.viewport.name} filter p95`,
    ).toBeLessThanOrEqual(result.budgets.filterP95Ms);
    expect(
      result.summary.longTaskTotalP95Ms,
      `${result.viewport.name} long-task total p95`,
    ).toBeLessThanOrEqual(result.budgets.longTaskTotalP95Ms);
    expect(
      result.summary.longestTaskP95Ms,
      `${result.viewport.name} longest task p95`,
    ).toBeLessThanOrEqual(result.budgets.longestTaskP95Ms);
    if (result.summary.retainedHeapDeltaP95Bytes !== null) {
      expect(
        result.summary.retainedHeapDeltaP95Bytes,
        `${result.viewport.name} retained heap delta p95`,
      ).toBeLessThanOrEqual(result.budgets.retainedHeapDeltaBytes);
    }
    if (result.summary.retainedHeapP95Bytes !== null) {
      expect(
        result.summary.retainedHeapP95Bytes,
        `${result.viewport.name} retained heap p95`,
      ).toBeLessThanOrEqual(result.budgets.retainedHeapBytes);
    }
  }
});
