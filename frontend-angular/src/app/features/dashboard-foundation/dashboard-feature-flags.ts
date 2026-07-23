import { Injectable, inject, signal } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { Observable, catchError, finalize, map, of, shareReplay, tap } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import { SystemFacade } from '../system/system.facade';

export interface DashboardFeatureFlags {
  readonly angularKanban: boolean;
  readonly angularModelDashboard: boolean;
}

export const CLOSED_DASHBOARD_FEATURE_FLAGS: DashboardFeatureFlags = Object.freeze({
  angularKanban: false,
  angularModelDashboard: false,
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
  private request?: Observable<DashboardFeatureFlags>;

  readonly flags = signal(CLOSED_DASHBOARD_FEATURE_FLAGS);
  readonly loaded = signal(false);
  readonly angularKanban = () => this.flags().angularKanban;
  readonly angularModelDashboard = () => this.flags().angularModelDashboard;

  ensureLoaded(force = false): Observable<DashboardFeatureFlags> {
    if (this.loaded() && !force) return of(this.flags());
    if (this.request && !force) return this.request;
    this.request = this.client.read().pipe(
      catchError(() => of(CLOSED_DASHBOARD_FEATURE_FLAGS)),
      tap(flags => {
        this.flags.set(flags);
        this.loaded.set(true);
      }),
      finalize(() => { this.request = undefined; }),
      shareReplay({ bufferSize: 1, refCount: false }),
    );
    return this.request;
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

