import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  prepareWindowBridgeBootstrap,
  WINDOW_BRIDGE_BOOTSTRAP,
} from './window-bridge-bootstrap';
import { WindowBridgeService } from './window-bridge.service';

describe('WindowBridgeService secure bootstrap', () => {
  afterEach(() => {
    TestBed.resetTestingModule();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    localStorage.clear();
    window.history.replaceState({}, '', '/');
  });

  it('clears stale auth before awaiting the authenticated loopback handoff', async () => {
    const bridgeUrl = 'http://127.0.0.1:43123';
    const bridgeToken = 'a'.repeat(32);
    localStorage.setItem('ananta.user.token', 'stale-hub-token');
    localStorage.setItem('ananta.oidc.access_token', 'stale-oidc-token');
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', role: 'hub', url: 'https://stale-hub.invalid', token: 'stale-agent-secret' },
    ]));
    window.history.replaceState(
      {},
      '',
      `/?bridge=${encodeURIComponent(bridgeUrl)}&hub_token=legacy-leak`
        + `#ananta_window_token=${bridgeToken}`,
    );

    let resolveFetch: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init).toMatchObject({
        cache: 'no-store',
        credentials: 'omit',
        headers: { 'X-Ananta-Window-Token': bridgeToken },
        referrerPolicy: 'no-referrer',
      });
      return new Promise<Response>(resolve => { resolveFetch = resolve; });
    });
    vi.stubGlobal('fetch', fetchMock);

    const pendingBootstrap = prepareWindowBridgeBootstrap();

    expect(localStorage.getItem('ananta.user.token')).toBeNull();
    expect(localStorage.getItem('ananta.oidc.access_token')).toBeNull();
    expect(localStorage.getItem('ananta.agents.v1')).not.toContain('stale-agent-secret');
    expect(window.location.href).not.toContain('bridge=');
    expect(window.location.href).not.toContain(bridgeToken);
    resolveFetch?.({
      ok: true,
      json: async () => ({
        ok: true,
        auth: {
          hub_url: 'https://hub.example.test',
          hub_token: 'hub-secret',
          oidc_token: 'oidc-secret',
        },
      }),
    } as Response);
    const bootstrap = await pendingBootstrap;

    expect(bootstrap).toEqual({
      bridgeUrl,
      windowToken: bridgeToken,
      auth: {
        hubUrl: 'https://hub.example.test',
        hubToken: 'hub-secret',
        oidcToken: 'oidc-secret',
      },
    });
    expect(localStorage.getItem('ananta.user.token')).toBe('hub-secret');
    expect(localStorage.getItem('ananta.oidc.access_token')).toBe('oidc-secret');
    expect(localStorage.getItem('ananta.agents.v1')).toContain('https://hub.example.test');

    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ state: { schema_version: 'v1', state_version: '1', payload: {} } }),
    } as Response);
    TestBed.configureTestingModule({ providers: [
      WindowBridgeService,
      { provide: WINDOW_BRIDGE_BOOTSTRAP, useValue: bootstrap },
    ] });
    const service = TestBed.inject(WindowBridgeService);
    await service.initFromUrlParams();

    expect(service.tuiAuthContext).toEqual(bootstrap?.auth);
    expect(fetchMock).toHaveBeenCalledWith(
      `${bridgeUrl}/auth-context`,
      expect.objectContaining({ cache: 'no-store' }),
    );
    service.ngOnDestroy();
  });

  it('rejects attacker bridge origins and scrubs legacy query credentials', async () => {
    localStorage.setItem('ananta.user.token', 'existing-session');
    window.history.replaceState(
      {},
      '',
      `/?bridge=https%3A%2F%2Fattacker.invalid&hub_token=hub-secret`
        + `#ananta_window_token=${'b'.repeat(32)}`,
    );
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(prepareWindowBridgeBootstrap()).resolves.toBeNull();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(localStorage.getItem('ananta.user.token')).toBe('existing-session');
    expect(window.location.href).not.toContain('attacker.invalid');
    expect(window.location.href).not.toContain('hub-secret');
  });

  it('rejects a Hub bearer that is not bound to an explicit Hub origin', async () => {
    const bridgeToken = 'e'.repeat(32);
    window.history.replaceState(
      {},
      '',
      `/?bridge=${encodeURIComponent('http://127.0.0.1:43123')}`
        + `#ananta_window_token=${bridgeToken}`,
    );
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        auth: { hub_url: '', hub_token: 'unbound-secret', oidc_token: '' },
      }),
    } as Response));

    await expect(prepareWindowBridgeBootstrap()).resolves.toBeNull();

    expect(localStorage.getItem('ananta.user.token')).toBeNull();
    expect(localStorage.getItem('ananta.agents.v1')).toBe('[]');
  });

  it('fails closed before fetch when the browser refuses URL scrubbing', async () => {
    localStorage.setItem('ananta.user.token', 'stale-hub-token');
    window.history.replaceState(
      {},
      '',
      `/?bridge=${encodeURIComponent('http://127.0.0.1:43123')}`
        + `#ananta_window_token=${'c'.repeat(32)}`,
    );
    vi.spyOn(window.history, 'replaceState').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(prepareWindowBridgeBootstrap()).resolves.toBeNull();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(localStorage.getItem('ananta.user.token')).toBeNull();
  });

  it('scrubs the capability even when stale auth storage cannot be cleared', async () => {
    const bridgeToken = 'd'.repeat(32);
    localStorage.setItem('ananta.user.token', 'stale-hub-token');
    window.history.replaceState(
      {},
      '',
      `/?bridge=${encodeURIComponent('http://127.0.0.1:43123')}`
        + `#ananta_window_token=${bridgeToken}`,
    );
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(prepareWindowBridgeBootstrap())
      .rejects.toThrow('window_bridge_auth_storage_unavailable');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.location.href).not.toContain('bridge=');
    expect(window.location.href).not.toContain(bridgeToken);
  });
});
