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
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { LoginComponent } from './login.component';
import { IdentityBridge } from '../services/identity/identity-bridge';
import { OidcAuthService } from '../services/oidc-auth.service';
import { UserAuthService } from '../services/user-auth.service';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { NetworkProfileService } from '../services/network-profile.service';
import { PythonRuntimeService } from '../services/python-runtime.service';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';

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
});