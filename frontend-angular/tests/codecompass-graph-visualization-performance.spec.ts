import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { expect, test, type Page, type TestInfo } from '@playwright/test';

import {
  GRAPH_VISUALIZATION_PERFORMANCE_COUNTS,
  createGraphVisualizationPerformanceArtifact,
} from '../src/app/features/codecompass-graph/testing/graph-visualization-performance.fixture';
import {
  createFunctionalGraphArtifact,
  installGraphApiMocks,
  installLocalGraphIdentity,
  openGraphInternals,
  trackHttpRequests,
  waitForTwoDimensionalRenderer,
} from './helpers/codecompass-graph-visualization';

type BudgetConfig = {
  schema: string;
  fixture: { nodes: number; edges: number; domains: number; hover_events: number };
  cache: { max_revision_profile_entries: number };
  operation_limits: Record<string, number>;
  browser_p95_ms: Record<string, number>;
};

type BrowserOperationProbe = {
  scoreNodeCalls: number;
  scoreEdgeCalls: number;
  projectionCalls: number;
  rendererRenderCalls: number;
  rendererIdentityChanges: number;
  graphReferenceResets: number;
};

const REPOSITORY_ROOT = fs.existsSync(path.resolve(process.cwd(), 'config/codecompass/graph_visualization_budgets.v1.json'))
  ? process.cwd()
  : path.resolve(process.cwd(), '..');
const BUDGET_PATH = path.join(REPOSITORY_ROOT, 'config/codecompass/graph_visualization_budgets.v1.json');
const SOURCE_PATHS = [
  '.github/workflows/dogfood-evidence.yml',
  'config/codecompass/graph_visualization_budgets.v1.json',
  'frontend-angular/playwright.ccgv-graph.config.ts',
  'frontend-angular/src/app/features/codecompass-graph/components/graph-2d-view/graph-2d-view.component.ts',
  'frontend-angular/src/app/features/codecompass-graph/components/graph-viewer/graph-viewer.component.ts',
  'frontend-angular/src/app/features/codecompass-graph/services/graph-metric-score.service.ts',
  'frontend-angular/src/app/features/codecompass-graph/services/graph-visual-projection.service.ts',
  'frontend-angular/src/app/features/codecompass-graph/testing/graph-visualization-performance.fixture.ts',
  'frontend-angular/tests/codecompass-graph-visualization-functional.spec.ts',
  'frontend-angular/tests/codecompass-graph-visualization-performance.spec.ts',
  'frontend-angular/tests/helpers/codecompass-graph-visualization.ts',
] as const;

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map(key =>
    `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(',')}}`;
}

function sha256(value: string): string {
  return createHash('sha256').update(value).digest('hex');
}

function percentile(values: readonly number[], percentileValue: number): number {
  if (!values.length) throw new Error('ccgv_performance_samples_required');
  const sorted = [...values].sort((left, right) => left - right);
  const rank = Math.max(1, Math.ceil(sorted.length * percentileValue / 100));
  return sorted[Math.min(rank, sorted.length) - 1];
}

function rounded(value: number): number {
  return Number(value.toFixed(6));
}

function readBudgets(): { bytes: Buffer; config: BudgetConfig; hash: string } {
  const bytes = fs.readFileSync(BUDGET_PATH);
  const config = JSON.parse(bytes.toString('utf8')) as BudgetConfig;
  expect(config.schema).toBe('ananta.codecompass-graph-visualization-budgets.v1');
  for (const [name, value] of Object.entries(config.browser_p95_ms)) {
    expect(Number.isFinite(value) && value > 0, `positive p95 budget: ${name}`).toBe(true);
  }
  for (const [name, value] of Object.entries(config.operation_limits)) {
    expect(Number.isInteger(value) && value >= 0, `non-negative operation budget: ${name}`).toBe(true);
  }
  return { bytes, config, hash: sha256(bytes.toString('utf8')) };
}

function sourceHashes(): Record<string, string> {
  return Object.fromEntries(SOURCE_PATHS.map(relativePath => {
    const absolutePath = path.join(REPOSITORY_ROOT, relativePath);
    if (!fs.existsSync(absolutePath)) throw new Error(`ccgv_source_missing:${relativePath}`);
    return [relativePath, sha256(fs.readFileSync(absolutePath, 'utf8'))];
  }));
}

async function browserProjectionMeasurements(page: Page, rawArtifact: unknown): Promise<{
  initial: number[];
  cached: number[];
  profile: number[];
  projectionHash: string;
  projectionRepeatHash: string;
  profileJson: string;
}> {
  return page.evaluate(async ({ artifact, sampleCount }) => {
    const viewerHost = document.querySelector('app-graph-viewer');
    const viewer = (window as any).ng.getComponent(viewerHost);
    if (!viewer?.adapter || !viewer?.projection || !viewer?.profiles) {
      throw new Error('ccgv_angular_debug_services_unavailable');
    }
    const graph = viewer.adapter.fromDomainArtifact(artifact);
    (window as any).__ccgvLargeGraph = graph;
    const projection = viewer.projection;
    const baseProfile = viewer.profiles.activeProfile();
    const collectGarbage = (globalThis as unknown as { gc?: () => void }).gc;
    if (typeof collectGarbage !== 'function') {
      throw new Error('ccgv_explicit_gc_unavailable');
    }

    const initial: number[] = [];
    for (let index = 0; index < sampleCount; index += 1) {
      projection.clearCache();
      // Consecutive samples allocate 20k immutable style records each. Collect
      // previous samples outside the timed region so this gate measures one
      // cold projection, not synthetic heap accumulation across 20 loads.
      collectGarbage();
      await new Promise<void>(resolve => setTimeout(resolve, 0));
      const started = performance.now();
      projection.project(graph, baseProfile);
      initial.push(performance.now() - started);
    }

    projection.clearCache();
    projection.project(graph, baseProfile);
    const cached: number[] = [];
    for (let index = 0; index < sampleCount; index += 1) {
      const started = performance.now();
      projection.project(graph, baseProfile);
      cached.push(performance.now() - started);
    }

    // A profile interaction reuses the revision-scoped normalization context;
    // only the profile projection is new. Clearing the whole cache here would
    // measure a graph-revision load, which belongs to initial_projection.
    projection.clearCache();
    projection.project(graph, baseProfile);
    const profile: number[] = [];
    for (let index = 0; index < sampleCount; index += 1) {
      const candidate = {
        ...baseProfile,
        profileId: `performance-${index}`,
        name: `Performance ${index}`,
        highlightFactors: {
          ...baseProfile.highlightFactors,
          hover: 1 + ((index + 1) / 100),
        },
      };
      const started = performance.now();
      projection.project(graph, candidate);
      profile.push(performance.now() - started);
    }

    const projectionDigest = async (value: unknown): Promise<string> => {
      const encoded = new TextEncoder().encode(JSON.stringify(value));
      const digest = await crypto.subtle.digest('SHA-256', encoded);
      return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
    };
    projection.clearCache();
    const firstProjection = projection.project(graph, baseProfile);
    const projectionHash = await projectionDigest(firstProjection);
    projection.clearCache();
    const repeatedProjection = projection.project(graph, baseProfile);
    const projectionRepeatHash = await projectionDigest(repeatedProjection);

    return {
      initial,
      cached,
      profile,
      projectionHash,
      projectionRepeatHash,
      profileJson: viewer.profiles.exportProfile(),
    };
  }, { artifact: rawArtifact, sampleCount: 20 });
}

async function measureCacheEviction(page: Page): Promise<{
  entries: number;
  deterministic: boolean;
}> {
  return page.evaluate(() => {
    const viewer = (window as any).ng.getComponent(document.querySelector('app-graph-viewer'));
    const projection = viewer.projection;
    const graph = (window as any).__ccgvLargeGraph;
    const base = viewer.profiles.activeProfile();
    const profiles = Array.from({ length: 10 }, (_, index) => ({
      ...base,
      profileId: `lru-${index}`,
      name: `LRU ${index}`,
      domainColorOverrides: {
        ...base.domainColorOverrides,
        'domain-00': `#${(index + 1).toString(16).padStart(6, '0')}`,
      },
    }));
    projection.clearCache();
    for (const profile of profiles) projection.project(graph, profile);
    const entries = projection.cacheStats().projectionEntries;

    const originalCompute = projection.computeProjection.bind(projection);
    let computations = 0;
    projection.computeProjection = (...args: unknown[]) => {
      computations += 1;
      return originalCompute(...args);
    };
    projection.project(graph, profiles[profiles.length - 1]);
    const newestWasCached = computations === 0;
    projection.project(graph, profiles[0]);
    const oldestWasEvicted = computations === 1;
    projection.computeProjection = originalCompute;
    return { entries, deterministic: newestWasCached && oldestWasEvicted };
  });
}

async function installBrowserOperationProbe(page: Page): Promise<void> {
  await page.evaluate(() => {
    const viewer = (window as any).ng.getComponent(document.querySelector('app-graph-viewer'));
    const renderer = (window as any).ng.getComponent(document.querySelector('app-graph-2d-view'));
    const projection = viewer.projection;
    const score = projection.scoreService;
    const counters: BrowserOperationProbe = {
      scoreNodeCalls: 0,
      scoreEdgeCalls: 0,
      projectionCalls: 0,
      rendererRenderCalls: 0,
      rendererIdentityChanges: 0,
      graphReferenceResets: 0,
    };
    const originalScoreNode = score.scoreNode.bind(score);
    const originalScoreEdge = score.scoreEdge.bind(score);
    const originalProject = projection.project.bind(projection);
    const originalRender = renderer._render.bind(renderer);
    score.scoreNode = (...args: unknown[]) => {
      counters.scoreNodeCalls += 1;
      return originalScoreNode(...args);
    };
    score.scoreEdge = (...args: unknown[]) => {
      counters.scoreEdgeCalls += 1;
      return originalScoreEdge(...args);
    };
    projection.project = (...args: unknown[]) => {
      counters.projectionCalls += 1;
      return originalProject(...args);
    };
    renderer._render = (...args: unknown[]) => {
      counters.rendererRenderCalls += 1;
      return originalRender(...args);
    };
    (window as any).__ccgvOperationProbe = {
      counters,
      renderer,
      rendererIdentity: renderer.cy,
      graphNodes: renderer.graph.nodes,
      graphEdges: renderer.graph.edges,
    };
  });
}

async function runBundledProfileInteraction(page: Page): Promise<{
  elapsed: number;
  projectionRuns: number;
  rendererReinitializations: number;
  graphDataResets: number;
}> {
  await page.getByTestId('graph-visual-settings-trigger').click();
  const slider = page.getByTestId('graph-visual-settings-drawer')
    .locator('section[aria-label="Hervorhebung"] input[type="range"]')
    .first();
  await expect(slider).toBeVisible();
  const result = await slider.evaluate(async element => {
    const probe = (window as any).__ccgvOperationProbe;
    const beforeProjection = probe.counters.projectionCalls;
    const beforeRender = probe.counters.rendererRenderCalls;
    const beforeRenderer = probe.renderer.cy;
    const beforeNodes = probe.renderer.graph.nodes;
    const beforeEdges = probe.renderer.graph.edges;
    const started = performance.now();
    for (const value of ['1.31', '1.32', '1.33', '1.34', '1.35']) {
      (element as HTMLInputElement).value = value;
      element.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    }
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    const rendererChanged = beforeRenderer !== probe.renderer.cy ? 1 : 0;
    const graphChanged = beforeNodes !== probe.renderer.graph.nodes || beforeEdges !== probe.renderer.graph.edges ? 1 : 0;
    probe.counters.rendererIdentityChanges += rendererChanged;
    probe.counters.graphReferenceResets += graphChanged;
    return {
      elapsed: performance.now() - started,
      projectionRuns: probe.counters.projectionCalls - beforeProjection,
      rendererReinitializations: (probe.counters.rendererRenderCalls - beforeRender) + rendererChanged,
      graphDataResets: graphChanged,
    };
  });
  await page.keyboard.press('Escape');
  return result;
}

async function runHoverBurst(page: Page, eventCount: number): Promise<{
  timings: number[];
  scoreRecomputations: number;
  rendererReinitializations: number;
}> {
  await page.getByTestId('graph-domain-legend-trigger').click();
  const entry = page.getByTestId('graph-domain-legend-drawer').locator('.domain-entry').first();
  await expect(entry).toBeVisible();
  const result = await entry.evaluate((element, count) => {
    const probe = (window as any).__ccgvOperationProbe;
    const beforeScores = probe.counters.scoreNodeCalls + probe.counters.scoreEdgeCalls;
    const beforeRender = probe.counters.rendererRenderCalls;
    const timings: number[] = [];
    for (let index = 0; index < count; index += 1) {
      const started = performance.now();
      element.dispatchEvent(new MouseEvent('mouseenter', { bubbles: false, composed: true }));
      element.dispatchEvent(new MouseEvent('mouseleave', { bubbles: false, composed: true }));
      timings.push(performance.now() - started);
    }
    return new Promise<{
      timings: number[];
      scoreRecomputations: number;
      rendererReinitializations: number;
    }>(resolve => requestAnimationFrame(() => resolve({
      timings,
      scoreRecomputations:
        (probe.counters.scoreNodeCalls + probe.counters.scoreEdgeCalls) - beforeScores,
      rendererReinitializations: probe.counters.rendererRenderCalls - beforeRender,
    })));
  }, eventCount);
  await page.keyboard.press('Escape');
  return result;
}

async function writeSuccessfulMeasurements(
  testInfo: TestInfo,
  measurements: Record<string, unknown>,
): Promise<void> {
  const outputPath = testInfo.outputPath('codecompass-graph-visualization-measurements.json');
  const serialized = `${JSON.stringify(measurements, null, 2)}\n`;
  fs.writeFileSync(outputPath, serialized, 'utf8');
  const handoffPath = process.env.CCGV_MEASUREMENTS_OUTPUT?.trim();
  if (handoffPath) {
    const resolvedHandoffPath = path.resolve(handoffPath);
    fs.mkdirSync(path.dirname(resolvedHandoffPath), { recursive: true });
    const temporaryPath = `${resolvedHandoffPath}.tmp-${process.pid}`;
    try {
      fs.writeFileSync(temporaryPath, serialized, { encoding: 'utf8', mode: 0o600 });
      fs.renameSync(temporaryPath, resolvedHandoffPath);
    } finally {
      if (fs.existsSync(temporaryPath)) fs.rmSync(temporaryPath);
    }
  }
  await testInfo.attach('codecompass-graph-visualization-measurements', {
    path: outputPath,
    contentType: 'application/json',
  });
}

test.describe.configure({ mode: 'serial', retries: 0 });

test('deterministic 5000/15000 browser gate stays within operation, cache and p95 budgets', async ({ page }, testInfo) => {
  test.setTimeout(240_000);
  const budgetBefore = readBudgets();
  const artifact = createGraphVisualizationPerformanceArtifact();
  const artifactRepeat = createGraphVisualizationPerformanceArtifact();
  const domains = new Set(artifact.nodes.map(node => String(node.attributes['domain_id'])));
  expect(artifact.nodes).toHaveLength(budgetBefore.config.fixture.nodes);
  expect(artifact.edges).toHaveLength(budgetBefore.config.fixture.edges);
  expect(domains.size).toBe(budgetBefore.config.fixture.domains);
  expect(GRAPH_VISUALIZATION_PERFORMANCE_COUNTS.hoverEvents)
    .toBe(budgetBefore.config.fixture.hover_events);

  const graphHash = sha256(canonicalJson(artifact));
  const graphRepeatHash = sha256(canonicalJson(artifactRepeat));
  expect(graphRepeatHash).toBe(graphHash);

  const requests = trackHttpRequests(page);
  await installLocalGraphIdentity(page);
  await installGraphApiMocks(page, createFunctionalGraphArtifact());
  await openGraphInternals(page);

  const browser = await browserProjectionMeasurements(page, artifact);
  expect(browser.projectionRepeatHash).toBe(browser.projectionHash);
  const profileHash = sha256(browser.profileJson);
  const initialP95 = rounded(percentile(browser.initial, 95));
  const cachedP95 = rounded(percentile(browser.cached, 95));
  const profileP95 = rounded(percentile(browser.profile, 95));
  expect(initialP95).toBeLessThanOrEqual(budgetBefore.config.browser_p95_ms['initial_projection']);
  expect(cachedP95).toBeLessThanOrEqual(budgetBefore.config.browser_p95_ms['cached_projection']);

  const cache = await measureCacheEviction(page);
  expect(cache.entries).toBe(budgetBefore.config.cache.max_revision_profile_entries);
  expect(cache.deterministic).toBe(true);

  await page.getByTestId('graph-view-mode-2d').click();
  await waitForTwoDimensionalRenderer(page);
  await installBrowserOperationProbe(page);
  const interactionRequestMark = requests.mark();
  const bundledProfile = await runBundledProfileInteraction(page);
  expect(bundledProfile.projectionRuns)
    .toBeLessThanOrEqual(budgetBefore.config.operation_limits['projection_runs_per_animation_frame']);
  expect(bundledProfile.rendererReinitializations)
    .toBeLessThanOrEqual(budgetBefore.config.operation_limits['renderer_reinitializations_per_profile_change']);
  expect(bundledProfile.graphDataResets)
    .toBeLessThanOrEqual(budgetBefore.config.operation_limits['graph_data_resets_per_profile_change']);
  expect(bundledProfile.elapsed)
    .toBeLessThanOrEqual(budgetBefore.config.browser_p95_ms['profile_update']);

  const hover = await runHoverBurst(page, GRAPH_VISUALIZATION_PERFORMANCE_COUNTS.hoverEvents);
  const hoverP95 = rounded(percentile(hover.timings, 95));
  expect(hover.scoreRecomputations)
    .toBeLessThanOrEqual(budgetBefore.config.operation_limits['score_recomputations_per_hover_burst']);
  expect(hover.rendererReinitializations)
    .toBeLessThanOrEqual(budgetBefore.config.operation_limits['renderer_reinitializations_per_profile_change']);
  expect(hoverP95).toBeLessThanOrEqual(budgetBefore.config.browser_p95_ms['hover_update']);
  expect(profileP95).toBeLessThanOrEqual(budgetBefore.config.browser_p95_ms['profile_update']);

  const visualHttpRequests = requests.graphSince(interactionRequestMark);
  expect(visualHttpRequests).toHaveLength(
    budgetBefore.config.operation_limits['http_requests_per_visual_interaction'],
  );
  expect(fs.readFileSync(BUDGET_PATH).equals(budgetBefore.bytes), 'budget file changed during gate').toBe(true);

  const measurements = {
    schema: 'ananta.codecompass-graph-visualization-browser-measurements.v1',
    environment_class: 'chromium-headless-angular-devserver-gc-stabilized',
    budget_sha256: budgetBefore.hash,
    fixture: {
      nodes: artifact.nodes.length,
      edges: artifact.edges.length,
      domains: domains.size,
      hover_events: GRAPH_VISUALIZATION_PERFORMANCE_COUNTS.hoverEvents,
    },
    operation_counts: {
      http_requests_per_visual_interaction: visualHttpRequests.length,
      score_recomputations_per_hover_burst: hover.scoreRecomputations,
      renderer_reinitializations_per_profile_change: bundledProfile.rendererReinitializations,
      graph_data_resets_per_profile_change: bundledProfile.graphDataResets,
      projection_runs_per_animation_frame: bundledProfile.projectionRuns,
    },
    browser_p95_ms: {
      initial_projection: initialP95,
      cached_projection: cachedP95,
      hover_update: hoverP95,
      profile_update: profileP95,
    },
    cache: {
      entries_after_eviction: cache.entries,
      deterministic_lru_passed: cache.deterministic,
    },
    hashes: {
      graph: graphHash,
      graph_repeat: graphRepeatHash,
      profile: profileHash,
      projection: browser.projectionHash,
      projection_repeat: browser.projectionRepeatHash,
    },
    source_hashes: sourceHashes(),
  };

  // The file is emitted only after every assertion above passed. It is a
  // measurement attachment, not the release evidence/report owned by the
  // repository-level gate runner.
  await writeSuccessfulMeasurements(testInfo, measurements);
});
