import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { NotificationService } from '../../../services/notification.service';
import { UserAuthService } from '../../../services/user-auth.service';
import { SystemFacade } from '../../system/system.facade';
import { ModelCatalogClient } from './model-catalog.client';
import { ModelRoutingMigrationStore } from './model-routing-migration.store';

describe('ModelRoutingMigrationStore', () => {
  let client: Record<string, ReturnType<typeof vi.fn>>;
  let store: ModelRoutingMigrationStore;

  beforeEach(() => {
    client = {
      readLegacyMigrationPreview: vi.fn(() => of({
        schema: 'ananta.model-routing-legacy-migration-preview.v1',
        current_revision: 4, applicable: true, idempotent: true,
        confirmation_digest: `sha256:${'a'.repeat(64)}`, entries: [],
        proposed_configuration: { schema: 'ananta.model-routing-config.v1', revision: 4, assignments: [], fallback_groups: [] },
        issues: [],
      })),
      readRoutingShadow: vi.fn(() => of({
        schema: 'ananta.model-routing-shadow-report.v1', configuration_revision: 4,
        matches: true, entries: [],
      })),
      readRoutingReleaseGate: vi.fn(() => of({
        schema: 'ananta.model-routing-release-gate.v1', configuration_revision: 4,
        ready: true, checks: [],
      })),
      readRoutingDiagnostics: vi.fn(() => of({
        schema: 'ananta.model-routing-diagnostics.v1', generated_at: '2026-08-25T00:00:00Z',
        configuration_revision: 4, catalog_revision: 2,
        unresolved_assignment_count: 0, non_executable_route_count: 0,
        contains_secrets: false, issues: [], usage: [],
      })),
      applyLegacyMigration: vi.fn(() => of({
        schema: 'ananta.model-routing-config.v1', revision: 5, assignments: [], fallback_groups: [],
      })),
    };
    TestBed.configureTestingModule({ providers: [
      ModelRoutingMigrationStore,
      { provide: ModelCatalogClient, useValue: client },
      { provide: SystemFacade, useValue: { resolveHubAgent: () => ({ url: 'http://hub' }) } },
      { provide: UserAuthService, useValue: {
        userPayload: { role: 'admin' }, user$: of({ role: 'admin' }),
      } },
      { provide: NotificationService, useValue: { success: vi.fn() } },
    ] });
    store = TestBed.inject(ModelRoutingMigrationStore);
    store.load();
  });

  it('requires an explicit confirmation before applying the exact preview revision and digest', () => {
    expect(store.canApply()).toBe(false);
    store.apply();
    expect(client['applyLegacyMigration']).not.toHaveBeenCalled();

    store.confirmed.set(true);
    expect(store.canApply()).toBe(true);
    store.apply();

    expect(client['applyLegacyMigration']).toHaveBeenCalledWith(
      'http://hub', 4, `sha256:${'a'.repeat(64)}`,
    );
  });

  it('fails closed for auth-disabled sessions and keeps diagnostics read-only', () => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      ModelRoutingMigrationStore,
      { provide: ModelCatalogClient, useValue: client },
      { provide: SystemFacade, useValue: { resolveHubAgent: () => ({ url: 'http://hub' }) } },
      { provide: UserAuthService, useValue: {
        userPayload: { role: 'admin', auth_mode: 'auth_disabled' },
        user$: of({ role: 'admin', auth_mode: 'auth_disabled' }),
      } },
      { provide: NotificationService, useValue: { success: vi.fn() } },
    ] });
    const denied = TestBed.inject(ModelRoutingMigrationStore);
    denied.load();
    denied.confirmed.set(true);

    expect(denied.canApply()).toBe(false);
    expect(denied.diagnostics()?.contains_secrets).toBe(false);
  });
});
