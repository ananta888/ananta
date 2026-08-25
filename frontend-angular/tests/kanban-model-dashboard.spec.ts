import AxeBuilder from '@axe-core/playwright';
import { expect, Page, Route, test } from '@playwright/test';

import { ADMIN_PASSWORD, ADMIN_USERNAME, HUB_URL } from './utils';

async function json(route: Route, data: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(status >= 400 ? data : { status: 'success', data }),
  });
}

async function expectAxeClean(page: Page, selector: string): Promise<void> {
  const result = await new AxeBuilder({ page })
    .include(selector)
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(result.violations).toEqual([]);
}

const columns = [
  { id: 'todo', title: 'To do', statuses: ['todo'], card_count: 1 },
  { id: 'in_progress', title: 'In progress', statuses: ['in_progress'], card_count: 0 },
  { id: 'blocked', title: 'Blocked', statuses: ['blocked_by_dependency'], card_count: 0 },
  { id: 'completed', title: 'Completed', statuses: ['completed'], card_count: 0 },
];

const task = {
  schema_version: 'kanban.v1',
  id: 'card-1',
  board_id: 'hub',
  title: 'E2E Karte',
  description: 'Serverseitiger Snapshot',
  status: 'todo',
  column_id: 'todo',
  position: 0,
  revision: 4,
  priority: 'Medium',
  assignee: null,
  labels: [],
  blocked: false,
  dependencies: [],
  comment_count: 1,
  activity_count: 1,
  created_at: '2026-07-23T10:00:00Z',
  updated_at: '2026-07-23T10:00:00Z',
};

async function authenticatedFixtures(page: Page): Promise<{
  moveCalls: () => number;
  refreshCalls: () => number;
  defaultCalls: () => number;
  searchQuery: () => string;
}> {
  const token = `e30.${Buffer.from(JSON.stringify({
    sub: 'e2e-admin',
    role: 'admin',
    exp: 4_102_444_800,
    capabilities: [
      'model_catalog.refresh', 'model_catalog.set_default', 'model_routing.read',
      'model_routing.validate', 'model_routing.export', 'model_routing.mutate',
    ],
  })).toString('base64url')}.sig`;
  await page.addInitScript(({ token }) => {
    localStorage.setItem('ananta.user.token', token);
    localStorage.setItem('ananta.shell.mode', 'advanced');
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', role: 'hub', url: 'http://127.0.0.1:5000', token: '' },
    ]));
  }, { token });

  let moves = 0;
  let refreshes = 0;
  let defaultSelections = 0;
  let latestSearchQuery = '';
  await page.route('**://127.0.0.1:5000/**', route => json(route, {}));
  await page.route('**/me', route => json(route, {
    sub: 'e2e-admin',
    role: 'admin',
    capabilities: [
      'model_catalog.refresh', 'model_catalog.set_default', 'model_routing.read',
      'model_routing.validate', 'model_routing.export', 'model_routing.mutate',
    ],
  }));
  await page.route('**/api/projects', route => json(route, {
    items: [{
      id: 'e2e-project', name: 'E2E Project', description: 'Deterministic browser fixture',
      status: 'active', is_active: true, origin: 'native', team_id: null,
      version: 1, created_at: 1, updated_at: 1, archived_at: null,
    }],
    count: 1,
  }));
  await page.route('**/config/features/v1', route => json(route, {
    schema: 'ananta.dashboard-feature-flags.v1',
    features: {
      angular_kanban: true,
      angular_model_dashboard: true,
      tui_kanban: false,
      tui_model_menu: false,
    },
  }));
  await page.route('**/api/v1/kanban/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/boards/hub/snapshot') && request.method() === 'GET') {
      return json(route, {
        schema_version: 'kanban.snapshot.v1',
        board: {
          id: 'hub', name: 'Hub task board', scope_type: 'hub', scope_id: null,
          revision: 'snapshot-1', card_count: 1,
          capabilities: ['kanban.read', 'kanban.write', 'kanban.comment'], columns,
        },
        cards: [task],
        event_sequence: 0,
      });
    }
    if (path.endsWith('/boards/hub/events') && request.method() === 'GET') {
      const requestUrl = new URL(request.url());
      const cursor = Number(
        requestUrl.searchParams.get('after_sequence')
          ?? request.headers()['last-event-id']
          ?? '0',
      );
      return json(route, {
        schema_version: 'kanban.event-batch.v1',
        board_id: 'hub',
        requested_after_sequence: cursor,
        next_after_sequence: cursor,
        latest_sequence: cursor,
        events: [],
        has_more: false,
        overflow: false,
        overflow_reason: null,
        gap_detected: false,
        gap_after_sequence: null,
        snapshot_required: false,
        snapshot_url: null,
        auth_renewal: { required: false, reason: null },
      });
    }
    if (path.endsWith('/boards') && request.method() === 'GET') {
      return json(route, { items: [{
        id: 'hub', name: 'Hub task board', scope_type: 'hub', scope_id: null,
        revision: 'snapshot-1', card_count: 1,
        capabilities: ['kanban.read', 'kanban.write', 'kanban.comment'],
      }], next_cursor: null });
    }
    if (path.endsWith('/boards/hub') && request.method() === 'GET') {
      return json(route, {
        id: 'hub', name: 'Hub task board', scope_type: 'hub', scope_id: null,
        revision: 'snapshot-1', card_count: 1,
        capabilities: ['kanban.read', 'kanban.write', 'kanban.comment'], columns,
      });
    }
    if (path.endsWith('/boards/hub/cards') && request.method() === 'GET') {
      latestSearchQuery = new URL(request.url()).searchParams.get('q') ?? '';
      return json(route, {
        board_id: 'hub', board_revision: 'snapshot-1', items: [task], next_cursor: null,
      });
    }
    if (path.endsWith('/boards/hub/cards/card-1') && request.method() === 'GET') {
      return json(route, task);
    }
    if (path.endsWith('/comments')) {
      return json(route, { items: [{
        id: 'comment-1', card_id: 'card-1', author_id: 'e2e-admin',
        body: 'Fixture Kommentar', created_at: task.created_at,
      }] });
    }
    if (path.endsWith('/activity')) {
      return json(route, { items: [{
        id: 'activity-1', card_id: 'card-1', event_type: 'kanban_card_created',
        actor_id: 'e2e-admin', message: 'Karte erstellt', details: {},
        created_at: task.created_at,
      }] });
    }
    if (path.endsWith('/commands/move')) {
      moves += 1;
      return json(route, {
        error: { code: 'kanban_revision_conflict', message: 'stale snapshot' },
      }, 409);
    }
    return json(route, task);
  });
  await page.route('**/models/catalog/v1', route => json(route, {
    schema: 'ananta.model-catalog.v1',
    default_selection: null,
    models: [{
      schema: 'ananta.model-summary.v1',
      provider_id: 'local',
      runtime: 'local',
      model_id: 'safe-model',
      display_name: 'Safe Model',
      availability: 'available',
      loaded: true,
      context_window: 8192,
      quantization: 'Q4',
      capabilities: ['chat'],
      health: 'healthy',
      is_default: false,
    }],
    provider_failures: [{ provider_id: 'offline-provider', reason_code: 'provider_timeout' }],
  }));
  await page.route('**/models/catalog/v1/refresh', route => {
    refreshes += 1;
    return json(route, {
      schema: 'ananta.model-catalog.v1',
      default_selection: null,
      models: [],
      provider_failures: [],
    });
  });
  await page.route('**/models/catalog/v2', route => json(route, {
    schema: 'ananta.model-catalog.v2',
    catalog_revision: 2,
    partial: true,
    models: [{
      schema: 'ananta.model-inventory-descriptor.v2',
      provider_id: 'local', model_id: 'safe-model', executor_id: 'api:local',
      display_name: 'Safe Model', runtime: 'local', source_ids: ['providers.catalog'],
      source_kinds: ['discovered'], profile_ids: ['safe-profile'], aliases: [],
      availability: 'available', health: 'healthy', configured: true,
      installed: true, loaded: true, listing_supported: true, auth_mode: null,
      auth_ready: null, context_window: 8192, quantization: 'Q4',
      capabilities: [{ capability_id: 'chat', value: 'supported', evidence: 'detected' }],
      conflicts: [], used_by_consumers: ['task.coding'],
    }],
    sources: [{
      source_id: 'offline-provider', source_kind: 'discovered', status: 'degraded',
      stale: false, from_cache: true, last_attempt_at: null, last_success_at: null,
      reason_code: 'provider_timeout', model_count: 1,
    }],
  }));
  await page.route('**/models/catalog/v2/refresh', route => {
    refreshes += 1;
    return json(route, {
      schema: 'ananta.model-catalog.v2', catalog_revision: 3, partial: false,
      models: [], sources: [],
    });
  });
  await page.route('**/models/consumers/v1', route => json(route, {
    schema: 'ananta.model-consumer-registry.v1',
    consumers: [{
      schema: 'ananta.model-consumer.v1', consumer_id: 'task.coding', label: 'Coding',
      category: 'tasks', required_capabilities: ['code'], allowed_scopes: ['global'], routable: true,
      default_model_role: 'coder', legacy_config_paths: [],
      mutation_capability: 'model_routing.mutate', registration_source: 'builtin',
      non_routable_reason: null,
    }],
  }));
  await page.route('**/models/routing/v1', route => json(route, {
    schema: 'ananta.model-routing-config.v1', revision: 0, assignments: [], fallback_groups: [],
  }));
  await page.route('**/models/default/v1', route => {
    defaultSelections += 1;
    return json(route, {
      schema: 'ananta.model-default-selection.v1',
      provider_id: 'local',
      model_id: 'safe-model',
    });
  });
  return {
    moveCalls: () => moves,
    refreshCalls: () => refreshes,
    defaultCalls: () => defaultSelections,
    searchQuery: () => latestSearchQuery,
  };
}

test('Kanban and model settings provide keyboard parity and Axe-clean desktop/mobile views', async ({ page }) => {
  const fixture = await authenticatedFixtures(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/board');
  await expect(page.getByTestId('kanban-board')).toBeVisible();
  await expectAxeClean(page, '.kanban-shell');

  const search = page.getByRole('searchbox', { name: 'Suche' });
  await search.focus();
  await expect(search).toBeFocused();
  await search.pressSequentially('E2E Karte');
  await expect.poll(fixture.searchQuery).toBe('E2E Karte');
  await search.press('ControlOrMeta+A');
  await search.press('Backspace');

  const cardButton = page.getByRole('button', {
    name: 'E2E Karte',
    exact: true,
  });
  await cardButton.focus();
  await expect(cardButton).toBeFocused();
  await cardButton.press('Enter');
  await expect(page.getByTestId('kanban-card-detail')).toBeVisible();
  await expect(page.getByText('Fixture Kommentar')).toBeVisible();
  await expectAxeClean(page, '[data-testid="kanban-card-detail"]');
  const closeDetail = page.getByRole('button', {
    name: 'Kartendetails schließen',
  });
  await closeDetail.focus();
  await expect(closeDetail).toBeFocused();
  await closeDetail.press('Enter');
  await expect(page.getByTestId('kanban-card-detail')).toBeHidden();

  const moveRight = page.getByRole('button', { name: 'Nach rechts' });
  await moveRight.focus();
  await expect(moveRight).toBeFocused();
  await moveRight.press('Enter');
  await expect.poll(fixture.moveCalls).toBe(1);
  await expect(page.getByText(/parallel geändert/)).toBeVisible();

  await page.goto('/settings?section=models');
  await expect(page.getByTestId('model-dashboard')).toBeVisible();
  await expect(page.getByText('offline-provider')).toBeVisible();
  await expectAxeClean(page, '.model-dashboard');
  const refreshProviders = page.getByRole('button', {
    name: 'Quellen aktualisieren',
  });
  await expect(refreshProviders).toBeEnabled();
  await refreshProviders.focus();
  await expect(refreshProviders).toBeFocused();
  await refreshProviders.press('Enter');
  await expect.poll(fixture.refreshCalls).toBe(1);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/board');
  await expect(page.getByTestId('kanban-board')).toBeVisible();
  await expect(page.locator('.kanban-column').first()).toBeVisible();
  await expectAxeClean(page, '.kanban-shell');

  await page.goto('/settings?section=models');
  await expect(page.getByTestId('model-dashboard')).toBeVisible();
  await expectAxeClean(page, '.model-dashboard');
});

test('feature flags recover after a real login replaces a rejected identity', async ({ page }) => {
  const rejectedToken = 'expired-e2e-identity';
  const loginToken = `e30.${Buffer.from(JSON.stringify({
    sub: 'e2e-admin', username: ADMIN_USERNAME, role: 'admin', exp: 4_102_444_800,
    capabilities: ['model_catalog.read'],
  })).toString('base64url')}.sig`;
  let rejectedFlagRequests = 0;
  let authenticatedFlagRequests = 0;

  await page.addInitScript(({ hubUrl, token }) => {
    localStorage.setItem('ananta.user.token', token);
    localStorage.setItem('ananta.shell.mode', 'advanced');
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', role: 'hub', url: hubUrl, token: '' },
    ]));
  }, { hubUrl: HUB_URL, token: rejectedToken });

  await page.route('**/me', route => {
    const authorization = route.request().headers()['authorization'] ?? '';
    if (authorization.includes(rejectedToken)) {
      return json(route, {
        sub: 'rejected-e2e-identity',
        username: ADMIN_USERNAME,
        role: 'admin',
        capabilities: [],
      });
    }
    return json(route, {
      sub: 'e2e-admin', username: ADMIN_USERNAME, role: 'admin',
      capabilities: ['model_catalog.read'],
    });
  });
  await page.route('**/api/network-profiles/**', route => {
    return json(route, {});
  });
  await page.route('**/login', route => json(route, {
    access_token: loginToken, refresh_token: 'e2e-refresh-token',
  }));
  await page.route('**/api/projects', route => json(route, {
    items: [{
      id: 'e2e-project', name: 'E2E Project', description: null, status: 'active',
      is_active: true, origin: 'native', team_id: null, version: 1,
      created_at: 1, updated_at: 1, archived_at: null,
    }],
    count: 1,
  }));
  await page.route('**/config/features/v1', async route => {
    const authorization = route.request().headers()['authorization'] ?? '';
    if (!authorization || authorization.includes(rejectedToken)) {
      rejectedFlagRequests += 1;
      return json(route, {
        error: { code: 'unauthorized', message: 'Rejected pre-login identity' },
      }, 401);
    }
    authenticatedFlagRequests += 1;
    return json(route, {
      schema: 'ananta.dashboard-feature-flags.v1',
      features: {
        angular_kanban: true,
        angular_model_dashboard: true,
        tui_kanban: false,
        tui_model_menu: false,
      },
    });
  });

  await page.goto('/dashboard');
  await expect.poll(() => rejectedFlagRequests).toBe(1);
  await expect(page.getByRole('heading', { name: 'Ananta Login' })).toBeVisible();

  await page.getByLabel('Benutzername').fill(ADMIN_USERNAME);
  await page.locator('input[name="password"]').fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: 'Anmelden', exact: true }).click();

  await expect(page).toHaveURL(/\/dashboard(?:[?#]|$)/);
  await expect.poll(() => authenticatedFlagRequests).toBe(1);
  await expect(page.locator('.app-nav a[href^="/board"]')).toHaveCount(1);
  expect(rejectedFlagRequests).toBe(1);
  expect(authenticatedFlagRequests).toBe(1);
});
