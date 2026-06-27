import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { firstValueFrom } from 'rxjs';
import { of } from 'rxjs';
import { IdentityRegistry } from './identity-registry';
import { HubIdentitySource } from './hub-identity-source';
import { OidcIdentitySource } from './oidc-identity-source';
import { UserAuthService } from '../user-auth.service';
import { OidcAuthService } from '../oidc-auth.service';
import { SecureTokenStorage } from '../secure-token-storage.service';
import { AgentDirectoryService } from '../agent-directory.service';
import { WebrtcSignalingService } from '../webrtc-signaling.service';
import { HttpClient } from '@angular/common/http';

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.sig`;
}

describe('IdentityRegistry', () => {
  let registry: IdentityRegistry;
  let hub: HubIdentitySource;
  let oidc: OidcIdentitySource;
  let signalingSvc: WebrtcSignalingService;

  beforeEach(() => {
    localStorage.clear();
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        IdentityRegistry,
        HubIdentitySource,
        OidcIdentitySource,
        UserAuthService,
        OidcAuthService,
        SecureTokenStorage,
        WebrtcSignalingService,
        { provide: HttpClient, useValue: { post: () => of({}), get: () => of({}) } },
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
      ],
    });
    registry = TestBed.inject(IdentityRegistry);
    hub = TestBed.inject(HubIdentitySource);
    oidc = TestBed.inject(OidcIdentitySource);
    signalingSvc = TestBed.inject(WebrtcSignalingService);
    TestBed.inject(SecureTokenStorage)._clearCacheForTesting();
  });

  describe('isAuthenticated', () => {
    it('starts as false when nothing restored', () => {
      expect(registry.isAuthenticated).toBe(false);
    });

    it('is true when hub is ready', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await hub.onAuthenticated(makeJwt({ sub: 'a', exp: future }), 'rt');
      expect(registry.isAuthenticated).toBe(true);
    });

    it('is true when oidc is ready', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await oidc.onAuthenticated(makeJwt({ sub: 'a', exp: future }), 'rt');
      expect(registry.isAuthenticated).toBe(true);
    });
  });

  describe('isAuthenticated$', () => {
    it('emits true after hub authenticated', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      const events: boolean[] = [];
      const sub = registry.isAuthenticated$.subscribe((v) => events.push(v));

      await hub.onAuthenticated(makeJwt({ sub: 'a', exp: future }), 'rt');
      // allow microtasks
      await new Promise((r) => setTimeout(r, 10));
      expect(events).toContain(true);
      sub.unsubscribe();
    });
  });

  describe('signaling source (derived from OIDC)', () => {
    it('is ready when oidc is ready', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await oidc.onAuthenticated(makeJwt({ sub: 'b', exp: future }), 'rt');
      const snap = await firstValueFrom(registry.signaling.snapshot$);
      expect(snap.status).toBe('ready');
    });

    it('is absent when oidc is absent', async () => {
      const snap = await firstValueFrom(registry.signaling.snapshot$);
      expect(snap.status).toBe('absent');
    });
  });

  describe('hardDisconnect on identity revocation', () => {
    it('hardDisconnects signaling when hub goes absent', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await hub.onAuthenticated(makeJwt({ sub: 'c', exp: future }), 'rt');
      const spy = vi.spyOn(signalingSvc, 'hardDisconnect');

      hub.logout();

      expect(spy).toHaveBeenCalled();
    });

    it('hardDisconnects signaling when oidc goes absent', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await oidc.onAuthenticated(makeJwt({ sub: 'd', exp: future }), 'rt');
      const spy = vi.spyOn(signalingSvc, 'hardDisconnect');

      oidc.logout();

      expect(spy).toHaveBeenCalled();
    });

    it('does NOT hardDisconnect if hub was already absent', async () => {
      const spy = vi.spyOn(signalingSvc, 'hardDisconnect');
      // hub was absent from the start, no transition absent→absent
      hub.logout();
      expect(spy).not.toHaveBeenCalled();
    });
  });

  describe('logoutAll', () => {
    it('logs out hub, oidc, hard-disconnects signaling, clears storage', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await hub.onAuthenticated(makeJwt({ sub: 'e', exp: future }), 'rt');
      await oidc.onAuthenticated(makeJwt({ sub: 'f', exp: future }), 'rt');

      registry.logoutAll();

      expect(hub.current.status).toBe('absent');
      expect(oidc.current.status).toBe('absent');
      expect(signalingSvc.status$.value).toBe('disconnected');
      expect(localStorage.getItem('ananta.user.token')).toBeNull();
    });
  });

  describe('restoreAllFromStorage', () => {
    it('restores hub and oidc from storage', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      localStorage.setItem('ananta.user.token', makeJwt({ sub: 'g', exp: future }));
      localStorage.setItem('ananta.oidc.access_token', makeJwt({ sub: 'h', exp: future }));

      await registry.restoreAllFromStorage();

      expect(hub.current.status).toBe('ready');
      expect(oidc.current.status).toBe('ready');
    });

    it('handles empty storage (both absent)', async () => {
      await registry.restoreAllFromStorage();
      expect(hub.current.status).toBe('absent');
      expect(oidc.current.status).toBe('absent');
    });
  });

  describe('snapshotAll', () => {
    it('returns a snapshot for every sphere', async () => {
      const snap = registry.snapshotAll();
      expect(snap).toHaveProperty('hub');
      expect(snap).toHaveProperty('oidc');
      expect(snap).toHaveProperty('signaling');
    });
  });
});