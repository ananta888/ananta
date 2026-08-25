import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Observable, Subject, of, throwError } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { UserAuthService } from '../../services/user-auth.service';
import {
  CLOSED_DASHBOARD_FEATURE_FLAGS,
  DashboardFeatureFlagClient,
  DashboardFeatureFlagStore,
  DashboardFeatureFlags,
  decodeDashboardFeatureFlags,
} from './dashboard-feature-flags';

const ENABLED: DashboardFeatureFlags = Object.freeze({
  angularKanban: true,
  angularModelDashboard: true,
  modelCatalogV2: true,
  modelRoutingEditor: true,
  legacyModelPickerDeprecation: true,
});

const KANBAN_ONLY: DashboardFeatureFlags = Object.freeze({
  angularKanban: true,
  angularModelDashboard: false,
  modelCatalogV2: false,
  modelRoutingEditor: false,
  legacyModelPickerDeprecation: false,
});

type Harness = {
  readonly store: DashboardFeatureFlagStore;
  readonly tokens: BehaviorSubject<string | null>;
  readonly read: ReturnType<typeof vi.fn>;
};

function setup(
  initialToken: string | null,
  responses: Observable<DashboardFeatureFlags>[],
): Harness {
  const tokens = new BehaviorSubject<string | null>(initialToken);
  const read = vi.fn(() => {
    const response = responses.shift();
    if (!response) throw new Error('Unexpected feature-flag request');
    return response;
  });
  TestBed.configureTestingModule({
    providers: [
      DashboardFeatureFlagStore,
      { provide: DashboardFeatureFlagClient, useValue: { read } },
      { provide: UserAuthService, useValue: { token$: tokens.asObservable() } },
    ],
  });
  return {
    store: TestBed.inject(DashboardFeatureFlagStore),
    tokens,
    read,
  };
}

afterEach(() => {
  TestBed.resetTestingModule();
});

describe('DashboardFeatureFlagStore identity lifecycle', () => {
  it('recovers from a pre-login 401 exactly once after login', () => {
    const harness = setup('expired-pre-login-token', [
      throwError(() => new Error('401 Unauthorized')),
      of(ENABLED),
    ]);

    expect(harness.store.flags()).toEqual(CLOSED_DASHBOARD_FEATURE_FLAGS);
    expect(harness.store.loaded()).toBe(true);
    expect(harness.read).toHaveBeenCalledTimes(1);

    harness.tokens.next('valid-login-token');

    expect(harness.store.flags()).toEqual(ENABLED);
    expect(harness.store.loaded()).toBe(true);
    expect(harness.read).toHaveBeenCalledTimes(2);
  });

  it('resets synchronously and reloads when the token rotates', () => {
    const rotated = new Subject<DashboardFeatureFlags>();
    const harness = setup('token-a', [of(ENABLED), rotated]);

    harness.tokens.next('token-b');

    expect(harness.store.flags()).toEqual(CLOSED_DASHBOARD_FEATURE_FLAGS);
    expect(harness.store.loaded()).toBe(false);
    expect(harness.read).toHaveBeenCalledTimes(2);

    rotated.next(KANBAN_ONLY);
    rotated.complete();

    expect(harness.store.flags()).toEqual(KANBAN_ONLY);
    expect(harness.store.loaded()).toBe(true);
  });

  it('stays fail-closed on logout without another request', () => {
    const harness = setup('valid-token', [of(ENABLED)]);

    harness.tokens.next(null);

    expect(harness.store.flags()).toEqual(CLOSED_DASHBOARD_FEATURE_FLAGS);
    expect(harness.store.loaded()).toBe(false);
    harness.store.ensureLoaded().subscribe(flags => {
      expect(flags).toEqual(CLOSED_DASHBOARD_FEATURE_FLAGS);
    });
    expect(harness.read).toHaveBeenCalledTimes(1);
  });

  it('ignores a stale response from the previous identity', () => {
    const stale = new Subject<DashboardFeatureFlags>();
    const current = new Subject<DashboardFeatureFlags>();
    const harness = setup('token-a', [stale, current]);

    harness.tokens.next('token-b');
    stale.next(ENABLED);
    stale.complete();

    expect(harness.store.flags()).toEqual(CLOSED_DASHBOARD_FEATURE_FLAGS);
    expect(harness.store.loaded()).toBe(false);

    current.next(KANBAN_ONLY);
    current.complete();

    expect(harness.store.flags()).toEqual(KANBAN_ONLY);
    expect(harness.store.loaded()).toBe(true);
  });

  it('deduplicates concurrent reload subscribers for one identity', () => {
    const pending = new Subject<DashboardFeatureFlags>();
    const harness = setup('valid-token', [pending]);
    const first = vi.fn();
    const second = vi.fn();

    harness.store.ensureLoaded().subscribe(first);
    harness.store.ensureLoaded().subscribe(second);

    expect(harness.read).toHaveBeenCalledTimes(1);
    pending.next(ENABLED);
    pending.complete();

    expect(first).toHaveBeenCalledWith(ENABLED);
    expect(second).toHaveBeenCalledWith(ENABLED);
  });
});

describe('dashboard feature flags', () => {
  it('fails closed for missing and string-valued flags', () => {
    expect(decodeDashboardFeatureFlags(undefined)).toEqual(CLOSED_DASHBOARD_FEATURE_FLAGS);
    expect(decodeDashboardFeatureFlags({
      schema: 'ananta.dashboard-feature-flags.v1',
      features: { angular_kanban: 'true', angular_model_dashboard: true },
    })).toEqual(CLOSED_DASHBOARD_FEATURE_FLAGS);
  });

  it('accepts only the versioned boolean projection', () => {
    expect(decodeDashboardFeatureFlags({
      status: 'success',
      data: {
        schema: 'ananta.dashboard-feature-flags.v1',
        features: {
          angular_kanban: true,
          angular_model_dashboard: false,
        },
      },
    })).toEqual(KANBAN_ONLY);
  });

  it('decodes independently staged model rollout flags', () => {
    expect(decodeDashboardFeatureFlags({
      schema: 'ananta.dashboard-feature-flags.v1',
      features: {
        angular_kanban: false,
        angular_model_dashboard: true,
        model_catalog_v2: true,
        model_routing_editor: false,
        legacy_model_picker_deprecation: true,
      },
    })).toMatchObject({
      angularModelDashboard: true,
      modelCatalogV2: true,
      modelRoutingEditor: false,
      legacyModelPickerDeprecation: true,
    });
  });
});
