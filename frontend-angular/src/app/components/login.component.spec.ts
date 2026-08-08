/**
 * Tests for the Self-Registration button rendered inside the LoginComponent.
 *
 * The button is a thin wrapper around OidcAuthService.registerWithKeycloak()
 * and must only render when IdentityBridge.showRegistration is true.
 *
 * Visibility rule (single source of truth):
 *   showRegistration = oidc.registration_allowed AND pair_enabled
 *                     AND a hub agent is registered locally.
 */
import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { Capacitor } from '@capacitor/core';
import { LoginComponent } from './login.component';
import { IdentityBridge } from '../services/identity/identity-bridge';
import { OidcAuthService } from '../services/oidc-auth.service';
import { UserAuthService } from '../services/user-auth.service';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { NetworkProfileService } from '../services/network-profile.service';
import { PythonRuntimeService } from '../services/python-runtime.service';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

function buildLogin(showRegistration: boolean, showOidc = true) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    imports: [LoginComponent],
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: IdentityBridge, useValue: { showRegistration, showOidcLogin: showOidc, showHubDirectLogin: true, hubLinkEnabled: false } },
      {
        provide: OidcAuthService,
        useValue: { registerWithKeycloak: vi.fn() },
      },
      { provide: UserAuthService, useValue: { token: null, oidcAccessTokenValue: null } },
      { provide: AgentDirectoryService, useValue: { list: () => [], upsert: () => undefined, get: () => undefined } },
      {
        provide: NetworkProfileService,
        useValue: {
          current: { profile_id: 'public-ananta', oidc: { issuer: 'https://kc.example/realms/r', client_id: 'cli', audience: 'aud', pkce_required: false } },
          load: async () => undefined,
          enablePublicPair: vi.fn().mockResolvedValue(undefined),
        },
      },
      {
        provide: PythonRuntimeService,
        useValue: { isNative: false },
      },
      {
        provide: ActivatedRoute,
        useValue: { snapshot: { queryParamMap: convertToParamMap({}) } },
      },
      { provide: Router, useValue: { navigate: vi.fn(), navigateByUrl: vi.fn() } },
    ],
  });
  return TestBed.createComponent(LoginComponent);
}

describe('LoginComponent — Self-Registration-Button', () => {
  it('rendert den Registrierungs-Button wenn IdentityBridge.showRegistration=true', () => {
    const fixture = buildLogin(true);
    fixture.detectChanges();
    fixture.detectChanges();
    const buttons = Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[];
    const labels = buttons.map((b) => b.textContent.trim());
    expect(labels.some((l) => /registrier|neues konto bei keycloak/i.test(l))).toBe(true);
  });

  it('rendert den Registrierungs-Button NICHT wenn showRegistration=false', () => {
    const fixture = buildLogin(false);
    fixture.detectChanges();
    fixture.detectChanges();
    const buttons = Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[];
    const labels = buttons.map((b) => b.textContent.trim());
    expect(labels.some((l) => /registrier|neues konto bei keycloak/i.test(l))).toBe(false);
  });

  it('aktiviert das öffentliche Profil erst nach bewusstem Klick', async () => {
    const fixture = buildLogin(false, false);
    fixture.detectChanges();
    const enablePublicPair = TestBed.inject(NetworkProfileService).enablePublicPair as ReturnType<typeof vi.fn>;
    const buttons = Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[];
    const optIn = buttons.find((button) => /öffentlichen pair-\/webrtc-zugang aktivieren/i.test(button.textContent));

    expect(optIn).toBeDefined();
    expect(enablePublicPair).not.toHaveBeenCalled();
    optIn!.click();
    await fixture.whenStable();

    expect(enablePublicPair).toHaveBeenCalledOnce();
  });

  it('klick auf Registrierungs-Button ruft OidcAuthService.registerWithKeycloak() auf', () => {
    const registerWithKeycloak = vi.fn();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [LoginComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: IdentityBridge, useValue: { showRegistration: true, showOidcLogin: true, showHubDirectLogin: true, hubLinkEnabled: false } },
        { provide: OidcAuthService, useValue: { registerWithKeycloak } },
        { provide: UserAuthService, useValue: { token: null, oidcAccessTokenValue: null } },
        { provide: AgentDirectoryService, useValue: { list: () => [], upsert: () => undefined, get: () => undefined } },
        { provide: NetworkProfileService, useValue: { current: { profile_id: 'public-ananta', oidc: { issuer: 'https://kc.example/realms/r', client_id: 'cli', audience: 'aud', pkce_required: false } }, load: async () => undefined } },
        { provide: PythonRuntimeService, useValue: { isNative: false } },
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap({}) } } },
        { provide: Router, useValue: { navigate: vi.fn(), navigateByUrl: vi.fn() } },
      ],
    });
    const fixture = TestBed.createComponent(LoginComponent);
    fixture.detectChanges();
    fixture.detectChanges();
    const buttons: HTMLButtonElement[] = Array.from(fixture.nativeElement.querySelectorAll('button'));
    const reg = buttons.find((b) => /registrier|neues konto bei keycloak/i.test(b.textContent.trim()));
    expect(reg).toBeDefined();
    reg!.click();
    expect(registerWithKeycloak).toHaveBeenCalledTimes(1);
  });

  it('verwendet auf Android einen Remote-Hub ohne den Embedded Hub zu starten', async () => {
    const ensureEmbeddedControlPlane = vi.fn().mockRejectedValue(new Error('must-not-run'));
    const setTokens = vi.fn().mockResolvedValue(undefined);
    const loadProfiles = vi.fn().mockResolvedValue(undefined);
    const navigate = vi.fn();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [LoginComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: IdentityBridge,
          useValue: {
            showRegistration: false,
            showOidcLogin: false,
            showHubDirectLogin: true,
            hubLinkEnabled: false,
          },
        },
        { provide: OidcAuthService, useValue: { registerWithKeycloak: vi.fn() } },
        {
          provide: UserAuthService,
          useValue: { token: null, oidcAccessTokenValue: null, setTokens },
        },
        {
          provide: AgentDirectoryService,
          useValue: {
            list: () => [
              { name: 'hub', role: 'hub', url: 'https://hub.example.test', token: '' },
            ],
            upsert: vi.fn(),
            get: vi.fn(),
          },
        },
        {
          provide: NetworkProfileService,
          useValue: { current: {}, load: loadProfiles },
        },
        {
          provide: PythonRuntimeService,
          useValue: { isNative: true, ensureEmbeddedControlPlane },
        },
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { queryParamMap: convertToParamMap({}) } },
        },
        { provide: Router, useValue: { navigate, navigateByUrl: vi.fn() } },
      ],
    });
    const fixture = TestBed.createComponent(LoginComponent);
    fixture.componentInstance.username = 'krusty';
    fixture.componentInstance.password = 'secret';

    const login = fixture.componentInstance.onLogin(new Event('submit'));
    const http = TestBed.inject(HttpTestingController);
    http.expectOne('https://hub.example.test/login').flush({
      data: { access_token: 'hub-token', refresh_token: 'refresh-token' },
    });
    await login;

    expect(ensureEmbeddedControlPlane).not.toHaveBeenCalled();
    expect(setTokens).toHaveBeenCalledWith('hub-token', 'refresh-token');
    expect(loadProfiles).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledWith(['/dashboard']);
    http.verify();
  });
});

function buildNativeLogin(hubUrl = 'http://127.0.0.1:5000') {
  const upsert = vi.fn();
  const ensureEmbeddedControlPlane = vi.fn().mockResolvedValue(undefined);
  const setTokens = vi.fn().mockResolvedValue(undefined);
  const loadProfiles = vi.fn().mockResolvedValue(undefined);
  const navigate = vi.fn();
  const hub = { name: 'hub', role: 'hub' as const, url: hubUrl };

  vi.spyOn(Capacitor, 'getPlatform').mockReturnValue('android');
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    imports: [LoginComponent],
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      {
        provide: IdentityBridge,
        useValue: {
          showRegistration: false,
          showOidcLogin: false,
          showHubDirectLogin: true,
          hubLinkEnabled: false,
        },
      },
      { provide: OidcAuthService, useValue: { registerWithKeycloak: vi.fn() } },
      {
        provide: UserAuthService,
        useValue: { token: null, oidcAccessTokenValue: null, setTokens },
      },
      {
        provide: AgentDirectoryService,
        useValue: {
          list: () => [hub],
          upsert,
          get: vi.fn(),
        },
      },
      {
        provide: NetworkProfileService,
        useValue: { current: {}, load: loadProfiles },
      },
      {
        provide: PythonRuntimeService,
        useValue: { isNative: true, ensureEmbeddedControlPlane },
      },
      {
        provide: ActivatedRoute,
        useValue: { snapshot: { queryParamMap: convertToParamMap({}) } },
      },
      { provide: Router, useValue: { navigate, navigateByUrl: vi.fn() } },
    ],
  });

  return {
    fixture: TestBed.createComponent(LoginComponent),
    upsert,
    ensureEmbeddedControlPlane,
    setTokens,
    loadProfiles,
    navigate,
  };
}

describe('LoginComponent — Android Hub-Onboarding', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the persisted Hub origin and Android connection guidance only in the native app', async () => {
    const native = buildNativeLogin('https://hub.example.test');
    native.fixture.detectChanges();
    await native.fixture.whenStable();
    native.fixture.detectChanges();

    const input = native.fixture.nativeElement.querySelector('#hubUrl') as HTMLInputElement | null;
    expect(native.fixture.componentInstance.hubUrl).toBe('https://hub.example.test');
    expect(input?.value).toBe('https://hub.example.test');
    expect(input?.placeholder).toBe('http://10.0.2.2:5000');
    expect(native.fixture.nativeElement.textContent).toContain('physischen Gerät');

    const browser = buildLogin(false, false);
    browser.detectChanges();
    expect(browser.nativeElement.querySelector('#hubUrl')).toBeNull();
  });

  it('normalizes and persists a valid origin as a Hub without URL credentials or token data', () => {
    const { fixture, upsert } = buildNativeLogin();
    fixture.detectChanges();
    fixture.componentInstance.hubUrl = ' https://Hub.Example.test:443/ ';

    fixture.componentInstance.saveNativeHubUrl();

    expect(upsert).toHaveBeenCalledWith({
      name: 'hub',
      role: 'hub',
      url: 'https://hub.example.test',
    });
    expect(fixture.componentInstance.hubUrl).toBe('https://hub.example.test');
    expect(fixture.componentInstance.hubUrlSavedInfo).toContain('https://hub.example.test');
  });

  it('blocks an invalid or credential-bearing Hub URL before any login request', async () => {
    const { fixture, upsert } = buildNativeLogin();
    fixture.detectChanges();
    fixture.componentInstance.hubUrl = 'https://user:secret@hub.example.test/api?token=secret';
    fixture.componentInstance.username = 'krusty';
    fixture.componentInstance.password = 'not-persisted';

    await fixture.componentInstance.onLogin(new Event('submit'));

    expect(upsert).not.toHaveBeenCalled();
    expect(fixture.componentInstance.error).toContain('ohne Zugangsdaten, Pfad, Query oder Fragment');
    expect(fixture.componentInstance.loading).toBe(false);
    TestBed.inject(HttpTestingController).expectNone(() => true);
  });

  it('persists the entered origin before login and sends the request to that normalized Hub', async () => {
    const {
      fixture,
      upsert,
      ensureEmbeddedControlPlane,
      setTokens,
      loadProfiles,
      navigate,
    } = buildNativeLogin();
    fixture.detectChanges();
    fixture.componentInstance.hubUrl = 'http://10.0.2.2:5000/';
    fixture.componentInstance.username = 'krusty';
    fixture.componentInstance.password = 'secret';

    const login = fixture.componentInstance.onLogin(new Event('submit'));
    const http = TestBed.inject(HttpTestingController);
    http.expectOne('http://10.0.2.2:5000/login').flush({
      data: { access_token: 'hub-token', refresh_token: 'refresh-token' },
    });
    await login;

    expect(upsert).toHaveBeenCalledWith({
      name: 'hub',
      role: 'hub',
      url: 'http://10.0.2.2:5000',
    });
    expect(ensureEmbeddedControlPlane).not.toHaveBeenCalled();
    expect(setTokens).toHaveBeenCalledWith('hub-token', 'refresh-token');
    expect(loadProfiles).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledWith(['/dashboard']);
    http.verify();
  });
});
