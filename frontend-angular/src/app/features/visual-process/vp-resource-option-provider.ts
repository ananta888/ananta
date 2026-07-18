import { HttpErrorResponse } from '@angular/common/http';
import { Injectable, InjectionToken, OnDestroy, Signal, inject, signal } from '@angular/core';
import { Observable, Subscription, TimeoutError, take, timeout } from 'rxjs';

export type VpResourceOptionStatus = 'loading' | 'ready' | 'empty' | 'degraded' | 'failed';

/** Deliberately narrow browser projection: no credentials or tenant authority data. */
export interface VpResourceOption {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
  stale?: boolean;
}
export interface VpRawResourceOption {
  id?: unknown;
  value?: unknown;
  name?: unknown;
  label?: unknown;
  description?: unknown;
  tenant_id?: unknown;
  disabled?: unknown;
  [key: string]: unknown;
}

export interface VpResourceOptionSnapshot {
  status: VpResourceOptionStatus;
  options: readonly VpResourceOption[];
  reason?: 'not_loaded' | 'forbidden' | 'not_found' | 'timeout' | 'load_failed' | 'resource_removed' | 'cache_invalidated';
  loadedAt?: number;
  expiresAt?: number;
}

export interface VpResourceOptionProviderConfig {
  ttlMs?: number;
  timeoutMs?: number;
  tenantId?: string;
}

interface ProviderEntry {
  state: ReturnType<typeof signal<VpResourceOptionSnapshot>>;
  loader?: () => Observable<readonly VpRawResourceOption[]>;
  config: Required<Pick<VpResourceOptionProviderConfig, 'ttlMs' | 'timeoutMs'>> & Pick<VpResourceOptionProviderConfig, 'tenantId'>;
}

export const VP_RESOURCE_OPTION_TTL_MS = new InjectionToken<number>('VP_RESOURCE_OPTION_TTL_MS', {
  providedIn: 'root', factory: () => 60_000,
});

@Injectable({ providedIn: 'root' })
export class VpResourceOptionProvider implements OnDestroy {
  private readonly defaultTtlMs = inject(VP_RESOURCE_OPTION_TTL_MS);
  private readonly entries = new Map<string, ProviderEntry>();
  private readonly subscriptions = new Subscription();

  register(
    resourceType: string,
    loader: () => Observable<readonly VpRawResourceOption[]>,
    config: VpResourceOptionProviderConfig = {},
  ): void {
    const entry = this.entry(resourceType);
    entry.loader = loader;
    entry.config = this.normalizedConfig(config);
  }

  state(resourceType: string): Signal<VpResourceOptionSnapshot> {
    return this.entry(resourceType).state.asReadonly();
  }

  snapshot(resourceType: string): VpResourceOptionSnapshot {
    return this.entry(resourceType).state();
  }

  load(resourceType: string, force = false): void {
    const entry = this.entry(resourceType);
    const current = entry.state();
    if (!force && current.expiresAt && current.expiresAt > Date.now() && ['ready', 'empty', 'degraded'].includes(current.status)) return;
    if (!entry.loader) {
      entry.state.set({ ...current, status: current.options.length ? 'degraded' : 'failed', reason: 'not_loaded' });
      return;
    }
    entry.state.set({ ...current, status: 'loading', reason: undefined });
    this.subscriptions.add(entry.loader().pipe(take(1), timeout(entry.config.timeoutMs)).subscribe({
      next: raw => this.commit(resourceType, raw),
      error: error => this.commitError(resourceType, error),
    }));
  }

  refresh(resourceType: string): void { this.load(resourceType, true); }

  invalidate(resourceType: string): void {
    const entry = this.entry(resourceType);
    const current = entry.state();
    entry.state.set({ ...current, status: current.options.length ? 'degraded' : 'failed', reason: 'cache_invalidated', expiresAt: 0 });
  }

  setStatic(resourceType: string, options: readonly VpRawResourceOption[], config: VpResourceOptionProviderConfig = {}): void {
    const entry = this.entry(resourceType);
    entry.config = this.normalizedConfig(config);
    this.commit(resourceType, options);
  }

  ngOnDestroy(): void { this.subscriptions.unsubscribe(); }

  private commit(resourceType: string, raw: readonly VpRawResourceOption[]): void {
    const entry = this.entry(resourceType);
    const previous = entry.state().options;
    const normalized = this.normalize(raw, entry.config.tenantId);
    const ids = new Set(normalized.map(option => option.value));
    const removed = previous.filter(option => !ids.has(option.value));
    const now = Date.now();
    if (removed.length) {
      entry.state.set({
        status: 'degraded', reason: 'resource_removed', loadedAt: now, expiresAt: now + entry.config.ttlMs,
        options: [...normalized, ...removed.map(option => ({ ...option, disabled: true, stale: true }))],
      });
      return;
    }
    entry.state.set({
      status: normalized.length ? 'ready' : 'empty', options: normalized,
      loadedAt: now, expiresAt: now + entry.config.ttlMs,
    });
  }

  private commitError(resourceType: string, error: unknown): void {
    const entry = this.entry(resourceType);
    const current = entry.state();
    let reason: VpResourceOptionSnapshot['reason'] = 'load_failed';
    if (error instanceof TimeoutError) reason = 'timeout';
    else if (error instanceof HttpErrorResponse && error.status === 403) reason = 'forbidden';
    else if (error instanceof HttpErrorResponse && error.status === 404) reason = 'not_found';
    const preserve = current.options.length > 0 && reason !== 'forbidden';
    entry.state.set({
      ...current,
      status: preserve ? 'degraded' : 'failed',
      options: preserve ? current.options.map(option => ({ ...option, stale: true })) : [],
      reason,
      expiresAt: 0,
    });
  }

  private normalize(raw: readonly VpRawResourceOption[], tenantId?: string): VpResourceOption[] {
    const options = raw.flatMap(item => {
      if (!item || typeof item !== 'object') return [];
      if (tenantId && typeof item.tenant_id === 'string' && item.tenant_id !== tenantId) return [];
      const value = String(item.value ?? item.id ?? '').trim();
      const label = String(item.label ?? item.name ?? value).trim();
      if (!value || !label) return [];
      return [{
        value,
        label,
        ...(typeof item.description === 'string' ? { description: item.description } : {}),
        ...(item.disabled === true ? { disabled: true } : {}),
      }];
    });
    return [...new Map(options.map(option => [option.value, option])).values()]
      .sort((left, right) => left.label.localeCompare(right.label) || left.value.localeCompare(right.value));
  }

  private entry(resourceType: string): ProviderEntry {
    const key = String(resourceType || '').trim();
    let entry = this.entries.get(key);
    if (!entry) {
      entry = {
        state: signal<VpResourceOptionSnapshot>({ status: 'failed', options: [], reason: 'not_loaded' }),
        config: this.normalizedConfig({}),
      };
      this.entries.set(key, entry);
    }
    return entry;
  }

  private normalizedConfig(config: VpResourceOptionProviderConfig): ProviderEntry['config'] {
    return {
      ttlMs: Math.max(0, config.ttlMs ?? this.defaultTtlMs),
      timeoutMs: Math.max(1, config.timeoutMs ?? 5_000),
      ...(config.tenantId ? { tenantId: config.tenantId } : {}),
    };
  }
}
