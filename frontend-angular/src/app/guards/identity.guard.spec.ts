import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { Router, UrlTree } from '@angular/router';
import { provideRouter, RouterModule } from '@angular/router';
import { runInInjectionContext } from '@angular/core';
import { identityGuard } from './identity.guard';
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

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.sig`;
}

async function runGuard(): Promise<boolean | UrlTree> {
  return TestBed.runInInjectionContext(async () => identityGuard({} as any, {} as any));
}

describe('identityGuard', () => {
  let registry: IdentityRegistry;
  let hub: HubIdentitySource;
  let router: Router;

  beforeEach(() => {
    localStorage.clear();
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
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
    router = TestBed.inject(Router);
    TestBed.inject(SecureTokenStorage)._clearCacheForTesting();
  });

  it('returns true when hub is authenticated', async () => {
    const future = Math.floor(Date.now() / 1000) + 3600;
    await hub.onAuthenticated(makeJwt({ sub: 'a', exp: future }), 'rt');
    const result = await runGuard();
    expect(result).toBe(true);
  });

  it('returns UrlTree("/login") when not authenticated', async () => {
    const result = await runGuard();
    expect(result instanceof UrlTree).toBe(true);
    const tree = result as UrlTree;
    expect(router.serializeUrl(tree)).toContain('/login');
  });

  it('returns UrlTree after logout', async () => {
    const future = Math.floor(Date.now() / 1000) + 3600;
    await hub.onAuthenticated(makeJwt({ sub: 'b', exp: future }), 'rt');
    let result = await runGuard();
    expect(result).toBe(true);

    registry.logoutAll();
    result = await runGuard();
    expect(result instanceof UrlTree).toBe(true);
  });
});