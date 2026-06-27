import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { APP_INITIALIZER } from '@angular/core';
import { IdentityRegistry } from '../services/identity/identity-registry';
import { HubIdentitySource } from '../services/identity/hub-identity-source';
import { OidcIdentitySource } from '../services/identity/oidc-identity-source';
import { UserAuthService } from '../services/user-auth.service';
import { OidcAuthService } from '../services/oidc-auth.service';
import { SecureTokenStorage } from '../services/secure-token-storage.service';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { WebrtcSignalingService } from '../services/webrtc-signaling.service';
import { HttpClient } from '@angular/common/http';
import { of } from 'rxjs';
import { identityRestoreInitializer } from './identity-restore.initializer';
import { NetworkProfileService } from '../services/network-profile.service';

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.sig`;
}

describe('identityRestoreInitializer', () => {
  let registry: IdentityRegistry;
  let hub: HubIdentitySource;
  let oidc: OidcIdentitySource;
  const profiles = { load: vi.fn(async () => undefined) };

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
        identityRestoreInitializer,
        { provide: NetworkProfileService, useValue: profiles },
        { provide: HttpClient, useValue: { post: () => of({}), get: () => of({}) } },
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
      ],
    });
    registry = TestBed.inject(IdentityRegistry);
    hub = TestBed.inject(HubIdentitySource);
    oidc = TestBed.inject(OidcIdentitySource);
    TestBed.inject(SecureTokenStorage)._clearCacheForTesting();
  });

  it('is registered as a multi APP_INITIALIZER', () => {
    const provider = identityRestoreInitializer as unknown as Record<string, unknown>;
    expect(provider['multi']).toBe(true);
    expect(typeof provider['useFactory']).toBe('function');
  });

  it('returned factory calls registry.restoreAllFromStorage', async () => {
    const future = Math.floor(Date.now() / 1000) + 3600;
    localStorage.setItem('ananta.user.token', makeJwt({ sub: 'a', exp: future }));
    localStorage.setItem('ananta.oidc.access_token', makeJwt({ sub: 'b', exp: future }));

    const provider = identityRestoreInitializer as unknown as {
      useFactory: (r: IdentityRegistry, p: NetworkProfileService) => () => Promise<void>;
    };
    const factory = provider.useFactory(registry, profiles as unknown as NetworkProfileService);
    await factory();

    expect(hub.current.status).toBe('ready');
    expect(oidc.current.status).toBe('ready');
    expect(profiles.load).toHaveBeenCalled();
  });

  it('factory handles empty storage gracefully', async () => {
    const provider = identityRestoreInitializer as unknown as {
      useFactory: (r: IdentityRegistry, p: NetworkProfileService) => () => Promise<void>;
    };
    const factory = provider.useFactory(registry, profiles as unknown as NetworkProfileService);
    await factory();
    expect(hub.current.status).toBe('absent');
    expect(oidc.current.status).toBe('absent');
  });
});
