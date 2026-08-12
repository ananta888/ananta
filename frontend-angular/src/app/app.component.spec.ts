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
import { ShareSessionService } from './services/share-session.service';
import { PairCompactAppSyncService } from './services/pair-compact-app-sync.service';

function configureShell(native = false) {
  TestBed.resetTestingModule();
  const hubSnapshot = new BehaviorSubject<IdentitySnapshot>({ status: 'absent' });
  const hubUser = new BehaviorSubject<Record<string, unknown> | null>(null);
  const rawHubToken = new BehaviorSubject<string | null>(null);
  const routeUrl = signal('/');
  const shareState = new BehaviorSubject({
    session: null as { id: string } | null,
    participants: [],
    messages: [],
    cursor: '0',
    role: null,
  });
  const publicPairRuntimeState = new BehaviorSubject<'idle' | 'public' | 'unknown'>('idle');
  const hubAgent = { name: 'hub', role: 'hub', url: 'https://hub.example.test' };
  const ensureSystemEvents = vi.fn();
  const disconnectSystemEvents = vi.fn();
  const compactPairStart = vi.fn();
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
          routeUrl,
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
      {
        provide: ShareSessionService,
        useValue: {
          state$: shareState,
          publicPairRuntimeState$: publicPairRuntimeState,
          get hasPublicPairRuntime() { return publicPairRuntimeState.value !== 'idle'; },
        },
      },
      { provide: PairCompactAppSyncService, useValue: { start: compactPairStart } },
    ],
  });
  TestBed.overrideComponent(AppComponent, {
    set: { imports: [AsyncPipe], schemas: [NO_ERRORS_SCHEMA] },
  });

  return {
    hubSnapshot,
    hubUser,
    rawHubToken,
    routeUrl,
    ensureSystemEvents,
    disconnectSystemEvents,
    shareState,
    publicPairRuntimeState,
    compactPairStart,
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
    expect(state.compactPairStart).toHaveBeenCalledOnce();

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

  it('keeps the AI-Snake Pair surface available on the public Pair route without Hub identity', () => {
    const state = configureShell();
    state.routeUrl.set('/pair-dev');
    const fixture = TestBed.createComponent(AppComponent);

    fixture.detectChanges();

    const assistant = (fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="assistant-feature-root"]');
    expect(assistant).not.toBeNull();
    expect((assistant as HTMLElement & { pairOnly?: boolean }).pairOnly).toBe(true);
  });

  it('restores the full AI-Snake tabs on Pair Dev when Hub identity is ready', () => {
    const state = configureShell();
    state.routeUrl.set('/pair-dev');
    state.rawHubToken.next('raw-hub-token');
    state.hubUser.next({ sub: 'hub-user', role: 'admin' });
    state.hubSnapshot.next({
      status: 'ready', token: 'raw-hub-token', subject: 'hub-user', issuer: 'hub',
    });
    const fixture = TestBed.createComponent(AppComponent);

    fixture.detectChanges();

    const assistant = (fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="assistant-feature-root"]');
    expect(assistant).not.toBeNull();
    expect((assistant as HTMLElement & { pairOnly?: boolean }).pairOnly).toBe(false);
  });

  it('keeps the Pair owner mounted after OIDC-only route navigation without exposing Hub UI', () => {
    const state = configureShell();
    state.routeUrl.set('/workspace');
    state.publicPairRuntimeState.next('public');
    state.shareState.next({
      ...state.shareState.value,
      session: { id: 'public-session-a' },
      role: 'participant',
    });
    const fixture = TestBed.createComponent(AppComponent);

    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const assistant = host.querySelector('[data-testid="assistant-feature-root"]');
    expect(assistant).not.toBeNull();
    expect((assistant as HTMLElement & { pairOnly?: boolean }).pairOnly).toBe(true);
    expect(host.querySelector('app-snake-overlay')).toBeNull();
    expect(host.querySelector('app-pair-remote-snake-overlay')).not.toBeNull();

    state.shareState.next({ ...state.shareState.value, session: null, role: null });
    state.publicPairRuntimeState.next('idle');
    fixture.detectChanges();
    expect(host.querySelector('[data-testid="assistant-feature-root"]')).toBeNull();
    expect(host.querySelector('app-pair-remote-snake-overlay')).toBeNull();
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
