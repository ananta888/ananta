import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CanActivateFn, Router } from '@angular/router';
import {
  EMPTY,
  Observable,
  catchError,
  distinctUntilChanged,
  finalize,
  map,
  of,
  shareReplay,
  switchMap,
  tap,
} from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import { UserAuthService } from '../../services/user-auth.service';
import { SystemFacade } from '../system/system.facade';

export interface DashboardFeatureFlags {
  readonly angularKanban: boolean;
  readonly angularModelDashboard: boolean;
  readonly modelCatalogV2: boolean;
  readonly modelRoutingEditor: boolean;
  readonly legacyModelPickerDeprecation: boolean;
}

export const CLOSED_DASHBOARD_FEATURE_FLAGS: DashboardFeatureFlags = Object.freeze({
  angularKanban: false,
  angularModelDashboard: false,
  modelCatalogV2: false,
  modelRoutingEditor: false,
  legacyModelPickerDeprecation: false,
});

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function dataOf(value: unknown): unknown {
  const outer = record(value);
  return outer && 'data' in outer ? outer['data'] : value;
}

export function decodeDashboardFeatureFlags(value: unknown): DashboardFeatureFlags {
  const body = record(dataOf(value));
  const features = record(body?.['features']);
  if (
    body?.['schema'] !== 'ananta.dashboard-feature-flags.v1'
    || typeof features?.['angular_kanban'] !== 'boolean'
    || typeof features?.['angular_model_dashboard'] !== 'boolean'
  ) {
    return CLOSED_DASHBOARD_FEATURE_FLAGS;
  }
  return Object.freeze({
    angularKanban: features['angular_kanban'],
    angularModelDashboard: features['angular_model_dashboard'],
    modelCatalogV2: typeof features['model_catalog_v2'] === 'boolean'
      ? features['model_catalog_v2'] : features['angular_model_dashboard'],
    modelRoutingEditor: typeof features['model_routing_editor'] === 'boolean'
      ? features['model_routing_editor'] : features['angular_model_dashboard'],
    legacyModelPickerDeprecation: typeof features['legacy_model_picker_deprecation'] === 'boolean'
      ? features['legacy_model_picker_deprecation'] : false,
  });
}

@Injectable({ providedIn: 'root' })
export class DashboardFeatureFlagClient {
  private readonly api = inject(HubApiCoreService);
  private readonly system = inject(SystemFacade);

  read(): Observable<DashboardFeatureFlags> {
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) return of(CLOSED_DASHBOARD_FEATURE_FLAGS);
    return this.api.get<unknown>(
      `${hub.url.replace(/\/$/, '')}/config/features/v1`,
      hub.url,
      undefined,
      false,
    ).pipe(map(decodeDashboardFeatureFlags));
  }
}

@Injectable({ providedIn: 'root' })
export class DashboardFeatureFlagStore {
  private readonly client = inject(DashboardFeatureFlagClient);
  private readonly auth = inject(UserAuthService);
  private readonly destroyRef = inject(DestroyRef);
  private request?: {
    identity: number;
    requestId: number;
    stream: Observable<DashboardFeatureFlags>;
  };
  private token: string | null = null;
  private identity = 0;
  private requestId = 0;

  readonly flags = signal(CLOSED_DASHBOARD_FEATURE_FLAGS);
  readonly loaded = signal(false);
  readonly angularKanban = () => this.flags().angularKanban;
  readonly angularModelDashboard = () => this.flags().angularModelDashboard;
  readonly modelCatalogV2 = () => this.flags().modelCatalogV2;
  readonly modelRoutingEditor = () => this.flags().modelRoutingEditor;
  readonly legacyModelPickerDeprecation = () => this.flags().legacyModelPickerDeprecation;

  constructor() {
    this.auth.token$
      .pipe(
        distinctUntilChanged(),
        tap(token => this.beginIdentity(token)),
        switchMap(() => this.token ? this.ensureLoaded() : EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();
  }

  ensureLoaded(force = false): Observable<DashboardFeatureFlags> {
    if (!this.token) return of(CLOSED_DASHBOARD_FEATURE_FLAGS);
    if (this.loaded() && !force) return of(this.flags());
    if (this.request?.identity === this.identity && !force) return this.request.stream;
    if (force) {
      this.requestId += 1;
      this.request = undefined;
      this.flags.set(CLOSED_DASHBOARD_FEATURE_FLAGS);
      this.loaded.set(false);
    }

    const identity = this.identity;
    const requestId = ++this.requestId;
    const stream = this.client.read().pipe(
      catchError(() => of(CLOSED_DASHBOARD_FEATURE_FLAGS)),
      map(flags => this.isCurrent(identity, requestId) ? flags : CLOSED_DASHBOARD_FEATURE_FLAGS),
      tap(flags => {
        if (this.isCurrent(identity, requestId)) {
          this.flags.set(flags);
          this.loaded.set(true);
        }
      }),
      finalize(() => {
        if (this.request?.identity === identity && this.request.requestId === requestId) {
          this.request = undefined;
        }
      }),
      shareReplay({ bufferSize: 1, refCount: true }),
    );
    this.request = { identity, requestId, stream };
    return stream;
  }

  private beginIdentity(value: string | null): void {
    this.identity += 1;
    this.requestId += 1;
    this.request = undefined;
    this.token = typeof value === 'string' && value.trim() ? value : null;
    this.flags.set(CLOSED_DASHBOARD_FEATURE_FLAGS);
    this.loaded.set(false);
  }

  private isCurrent(identity: number, requestId: number): boolean {
    return Boolean(this.token) && this.identity === identity && this.requestId === requestId;
  }
}

export const angularKanbanGuard: CanActivateFn = () => {
  const flags = inject(DashboardFeatureFlagStore);
  const router = inject(Router);
  return flags.ensureLoaded().pipe(
    map(value => value.angularKanban ? true : router.createUrlTree(['/workspace'])),
    catchError(() => of(router.createUrlTree(['/workspace']))),
  );
};
