import { Inject, inject, Injectable, InjectionToken } from '@angular/core';
import {
  GraphVisualProfileStoragePort,
  GraphVisualProfileStorageResult,
} from './graph-visual-profile-storage.port';

export interface BrowserKeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const STORAGE_PREFIX = 'ananta.codecompass.graph-visual-profile.v1';

function contextHash(value: string): string {
  let hash = 0xcbf29ce484222325n;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= BigInt(value.charCodeAt(index));
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return hash.toString(16).padStart(16, '0');
}

function browserStorage(): BrowserKeyValueStorage | null {
  try {
    return typeof globalThis !== 'undefined' && 'localStorage' in globalThis
      ? globalThis.localStorage
      : null;
  } catch {
    return null;
  }
}

export const GRAPH_VISUAL_PROFILE_BROWSER_STORAGE = new InjectionToken<BrowserKeyValueStorage | null>(
  'GRAPH_VISUAL_PROFILE_BROWSER_STORAGE',
  {
    providedIn: 'root',
    factory: browserStorage,
  },
);

@Injectable({ providedIn: 'root' })
export class LocalStorageGraphVisualProfileAdapter implements GraphVisualProfileStoragePort {
  constructor(
    @Inject(GRAPH_VISUAL_PROFILE_BROWSER_STORAGE)
    private readonly storage: BrowserKeyValueStorage | null,
  ) {}

  load(contextKey: string): GraphVisualProfileStorageResult {
    if (!this.storage) return { ok: false, value: null, reasonCode: 'storage_unavailable' };
    try {
      return { ok: true, value: this.storage.getItem(this.key(contextKey)) };
    } catch {
      return { ok: false, value: null, reasonCode: 'storage_read_failed' };
    }
  }

  save(contextKey: string, canonicalProfileJson: string): GraphVisualProfileStorageResult {
    if (!this.storage) return { ok: false, value: null, reasonCode: 'storage_unavailable' };
    try {
      this.storage.setItem(this.key(contextKey), canonicalProfileJson);
      return { ok: true, value: canonicalProfileJson };
    } catch (error) {
      return {
        ok: false,
        value: null,
        reasonCode: this.isQuotaError(error) ? 'storage_quota_exceeded' : 'storage_write_failed',
      };
    }
  }

  remove(contextKey: string): GraphVisualProfileStorageResult {
    if (!this.storage) return { ok: false, value: null, reasonCode: 'storage_unavailable' };
    try {
      this.storage.removeItem(this.key(contextKey));
      return { ok: true, value: null };
    } catch {
      return { ok: false, value: null, reasonCode: 'storage_remove_failed' };
    }
  }

  storageKey(contextKey: string): string {
    return this.key(contextKey);
  }

  private key(contextKey: string): string {
    return `${STORAGE_PREFIX}.${contextHash(contextKey.trim() || 'default')}`;
  }

  private isQuotaError(error: unknown): boolean {
    return typeof DOMException !== 'undefined' && error instanceof DOMException
      ? error.name === 'QuotaExceededError' || error.code === 22
      : Boolean(error && typeof error === 'object' && (error as { name?: string }).name === 'QuotaExceededError');
  }
}

export const GRAPH_VISUAL_PROFILE_STORAGE = new InjectionToken<GraphVisualProfileStoragePort>(
  'GRAPH_VISUAL_PROFILE_STORAGE',
  {
    providedIn: 'root',
    factory: () => inject(LocalStorageGraphVisualProfileAdapter),
  },
);
