import { AsyncPipe } from '@angular/common';
import { NO_ERRORS_SCHEMA, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { Capacitor } from '@capacitor/core';
import { BehaviorSubject, of } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AppComponent } from './app.component';
import { AgentDirectoryService } from './services/agent-directory.service';
import { AppShellStateService } from './services/app-shell-state.service';
import { IdentityRegistry } from './services/identity/identity-registry';
import type { IdentitySnapshot } from './services/identity/identity.types';
import { MobileRuntimeService } from './services/mobile-runtime.service';
import { ProjectContextService } from './services/project-context.service';
import { PythonRuntimeService } from './services/python-runtime.service';
import { SnakeOverlayService } from './services/snake-overlay.service';
import { SystemFacade } from './features/system/system.facade';
import { UserAuthService } from './services/user-auth.service';
import { WindowBridgeService } from './services/window-bridge.service';

function configureShell(native = false) {
  TestBed.resetTestingModule();
  const hubSnapshot = new BehaviorSubject<IdentitySnapshot>({ status: 'absent' });
  const hubUser = new BehaviorSubject<Record<string, unknown> | null>(null);
  const rawHubToken = new BehaviorSubject<string | null>(null);
  const hubAgent = { name: 'hub', role: 'hub', url: 'https://hub.example.test' };
  const ensureSystemEvents = vi.fn();
  const disconnectSystemEvents = vi.fn();
  const auth = {
    token$: rawHubToken.asObservable(),
    user$: hubUser.asObservable(),
    get token() { return rawHubToken.value; },
    get userPayload() { return hubUser.value; },
    decodeTokenPayload: (token: string | null) => (
      token === rawHubToken.value ? hubUser.value : null
    ),
    isLoggedIn: () => rawHubToken.value !== null,
    logout: vi.fn(),
    setTokens: vi.fn(),
    setOidcAccessToken: vi.fn(),
  };

  TestBed.configureTestingModule({
    providers: [
      { provide: AgentDirectoryService, useValue: { list: () => [hubAgent], upsert: vi.fn() } },
      { provide: UserAuthService, useValue: auth },
      { provide: Router, useValue: { navigate: vi.fn(), url: '/' } },
      { provide: MobileRuntimeService, useValue: { isNative: native } },
      {
        provide: SystemFacade,
        useValue: {
          disconnectSystemEvents,
          resolveHubAgent: () => hubAgent,
          ensureSystemEvents,
        },
      },
      {
        provide: AppShellStateService,
        useValue: {
          init: vi.fn(),
          mobileNavOpen: signal(false),
          darkMode: signal(false),
          mode: signal<'simple' | 'advanced'>('simple'),
          routeUrl: signal('/'),
          navGroups: () => [{ label: 'Hub', items: [{ path: '/hub', label: 'Hub only' }] }],
          toggleMobileNav: vi.fn(),
          closeMobileNav: vi.fn(),
          openMobileNav: vi.fn(),
          toggleDarkMode: vi.fn(),
          toggleMode: vi.fn(),
        },
      },
      { provide: PythonRuntimeService, useValue: { ensureEmbeddedControlPlane: vi.fn() } },
      {
        provide: WindowBridgeService,
        useValue: { initFromUrlParams: () => Promise.resolve(), tuiAuthContext: {} },
      },
      { provide: SnakeOverlayService, useValue: { visible$: of(false), toggle: vi.fn() } },
      {
        provide: ProjectContextService,
        useValue: { selectedProjectId: signal<string | null>(null) },
      },
      {
        provide: IdentityRegistry,
        useValue: {
          hub: {
            snapshot$: hubSnapshot.asObservable(),
            get current() { return hubSnapshot.value; },
          },
        },
      },
    ],
  });
  TestBed.overrideComponent(AppComponent, {
    set: { imports: [AsyncPipe], schemas: [NO_ERRORS_SCHEMA] },
  });

  return {
    hubSnapshot,
    hubUser,
    rawHubToken,
    ensureSystemEvents,
    disconnectSystemEvents,
  };
}

describe('AppComponent Hub shell trust boundary', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    TestBed.resetTestingModule();
  });

  it('reacts to validated Hub ready/expired transitions and starts events only once per ready period', () => {
    const state = configureShell();
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    const host = fixture.nativeElement as HTMLElement;

    expect(host.querySelector('[data-testid="public-pair-nav-link"]')?.textContent).toContain('Public Pair Dev');
    expect(host.querySelector('#primary-navigation')).toBeNull();
    expect(host.querySelector('[data-testid="assistant-feature-root"]')).toBeNull();

    state.rawHubToken.next('raw-hub-token');
    state.hubUser.next({ sub: 'hub-user', role: 'admin' });
    state.hubSnapshot.next({
      status: 'ready', token: 'raw-hub-token', subject: 'hub-user', issuer: 'hub',
    });
    fixture.detectChanges();

    expect(host.querySelector('#primary-navigation')).not.toBeNull();
    expect(host.querySelector('[data-testid="assistant-feature-root"]')).not.toBeNull();
    expect(host.querySelector('.app-header-user')?.textContent).toContain('hub-user (admin)');
    expect(state.ensureSystemEvents).toHaveBeenCalledOnce();

    state.hubSnapshot.next({
      status: 'ready', token: 'raw-hub-token', subject: 'hub-user', issuer: 'hub',
    });
    fixture.detectChanges();
    expect(state.ensureSystemEvents).toHaveBeenCalledOnce();

    state.hubSnapshot.next({ status: 'expired', error: 'hub_access_token_expired' });
    fixture.detectChanges();

    expect(host.querySelector('#primary-navigation')).toBeNull();
    expect(host.querySelector('[data-testid="assistant-feature-root"]')).toBeNull();
    expect(host.querySelector('.app-hright')).toBeNull();
  });

  it('does not render Android Hub navigation for an expired raw Hub token', () => {
    vi.spyOn(Capacitor, 'getPlatform').mockReturnValue('android');
    const state = configureShell(true);
    state.rawHubToken.next('expired-but-still-stored');
    state.hubUser.next({ sub: 'stale-admin', role: 'admin' });
    state.hubSnapshot.next({ status: 'expired', error: 'hub_access_token_expired' });

    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    const host = fixture.nativeElement as HTMLElement;

    expect(host.querySelector('.android-fullscreen-menu')).toBeNull();
    expect(host.querySelector('.android-edge-toggle')).toBeNull();
    expect(host.querySelector('[data-testid="assistant-feature-root"]')).toBeNull();
  });
});
