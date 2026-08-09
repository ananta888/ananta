import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { OidcRefreshLock } from './oidc-refresh-lock.service';

describe('OidcRefreshLock', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    TestBed.resetTestingModule();
  });

  it('uses an exclusive same-origin browser lock when available', async () => {
    const request = vi.fn(async <T>(
      name: string,
      options: LockOptions,
      callback: () => Promise<T>,
    ) => {
      expect(name).toBe('ananta.oidc.refresh.v1');
      expect(options).toEqual({ mode: 'exclusive' });
      return callback();
    });
    vi.stubGlobal('navigator', { ...navigator, locks: { request } });
    const lock = TestBed.inject(OidcRefreshLock);
    const operation = vi.fn(async () => 'refreshed');

    await expect(lock.run(operation)).resolves.toBe('refreshed');

    expect(request).toHaveBeenCalledOnce();
    expect(operation).toHaveBeenCalledOnce();
  });

  it('uses the compare-and-swap fallback when Web Locks are unavailable', async () => {
    vi.stubGlobal('navigator', { ...navigator, locks: undefined });
    const lock = TestBed.inject(OidcRefreshLock);
    const operation = vi.fn(async () => 'fallback');

    await expect(lock.run(operation)).resolves.toBe('fallback');

    expect(operation).toHaveBeenCalledOnce();
  });

  it('serializes login and refresh operations even without Web Locks', async () => {
    vi.stubGlobal('navigator', { ...navigator, locks: undefined });
    const lock = TestBed.inject(OidcRefreshLock);
    let releaseLogin!: () => void;
    const login = lock.run(async () => {
      await new Promise<void>((resolve) => { releaseLogin = resolve; });
      return 'login';
    });
    const refreshOperation = vi.fn(async () => 'refresh');
    const refresh = lock.run(refreshOperation);
    await Promise.resolve();

    expect(refreshOperation).not.toHaveBeenCalled();
    releaseLogin();

    await expect(login).resolves.toBe('login');
    await expect(refresh).resolves.toBe('refresh');
    expect(refreshOperation).toHaveBeenCalledOnce();
  });
});
