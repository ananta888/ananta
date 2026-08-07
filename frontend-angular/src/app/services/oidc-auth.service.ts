/** T12 / T13 / T14 / T15 / T16: OIDC PKCE + Device Flow + Refresh + Logout + Nonce. */
import { Injectable, inject } from '@angular/core';
import { sha256Bytes } from '../shared/crypto/sha256';
import { Router } from '@angular/router';
import { map } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';
import { NetworkProfileService } from './network-profile.service';
import { UserAuthService } from './user-auth.service';
import {
  PUBLIC_OIDC_CLIENT_ID,
  PUBLIC_OIDC_ISSUER,
} from './public-ananta-endpoints';

const SCOPES = 'openid profile email';
const SS_PKCE_KEY = 'oidc.pkce';       // sessionStorage
const SS_NONCE_KEY = 'oidc.nonce';
const LS_POPUP_KEY = 'oidc.pkce.popup'; // localStorage — shared with popup window
const LOGIN_POPUP_FEATURES = 'width=560,height=680,left=200,top=80';
const POPUP_DISCOVERY_TIMEOUT_MS = 10_000;

export type OidcPopupLoginFailure =
  | 'popup_blocked'
  | 'configuration_missing'
  | 'popup_closed'
  | 'issuer_unreachable'
  | 'popup_start_failed';

/** Stable, user-facing failure contract for callers that render login feedback. */
export class OidcPopupLoginError extends Error {
  override readonly name = 'OidcPopupLoginError';

  constructor(
    readonly code: OidcPopupLoginFailure,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
  }
}

interface OidcMeta {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint: string;
  device_authorization_endpoint?: string;
}

interface DeviceAuthResponse {
  device_code: string;
  user_code: string;
  verification_uri: string;
  verification_uri_complete?: string;
  expires_in: number;
  interval: number;
}

@Injectable({ providedIn: 'root' })
export class OidcAuthService {
  private userAuth = inject(UserAuthService);
  private dir = inject(AgentDirectoryService);
  private profiles = inject(NetworkProfileService);
  private router = inject(Router);

  private _meta: OidcMeta | null = null;
  private _metaIssuer = '';
  private _sessionNonce = '';

  readonly loggedIn$ = this.userAuth.oidcToken$.pipe(map(t => !!t));

  get sessionNonce(): string { return this._sessionNonce; }
  get hasNonce(): boolean { return !!this._sessionNonce; }

  get issuer(): string {
    const configured = this.profiles.current?.oidc?.issuer;
    return (typeof configured === 'string' ? configured.trim() : '') || PUBLIC_OIDC_ISSUER;
  }

  get clientId(): string {
    const configured = this.profiles.current?.oidc?.client_id;
    return (typeof configured === 'string' ? configured.trim() : '') || PUBLIC_OIDC_CLIENT_ID;
  }

  private get hubUrl(): string {
    return this.dir.list().find((agent) => agent.role === 'hub')?.url || '';
  }

  get currentUsername(): string {
    const p = this.userAuth.userPayload;
    return String(p?.preferred_username || p?.email || p?.sub || '');
  }

  constructor() {
    // Sync token written by popup window (popup → parent via localStorage storage event)
    if (!window.opener) {
      window.addEventListener('storage', (e: StorageEvent) => {
        if (e.key === 'ananta.user.token' && e.newValue) {
          const refresh = localStorage.getItem('ananta.user.refresh_token') ?? undefined;
          this.userAuth.setTokens(e.newValue, refresh);
        } else if (e.key === 'ananta.oidc.access_token') {
          this.userAuth.setOidcAccessToken(e.newValue);
          if (e.newValue) {
            void this.tryRestoreLinkedHubSession(e.newValue);
          }
        } else if (e.key === 'oidc.popup.nonce' && e.newValue) {
          this._sessionNonce = e.newValue;
          localStorage.removeItem('oidc.popup.nonce');
        }
      });
    }
  }

  // ── Discovery ────────────────────────────────────────────────────────

  private async loadMeta(issuer = this.issuer, timeoutMs = 0): Promise<OidcMeta> {
    const normalizedIssuer = this.normalizeHttpUrl(issuer, 'OIDC issuer', true);
    if (this._meta && this._metaIssuer === normalizedIssuer) return this._meta;
    const controller = timeoutMs > 0 ? new AbortController() : null;
    const timeout = controller
      ? window.setTimeout(() => controller.abort(), timeoutMs)
      : undefined;
    try {
      const r = await fetch(
        `${normalizedIssuer}/.well-known/openid-configuration`,
        controller ? { signal: controller.signal } : undefined,
      );
      if (!r.ok) throw new Error(`OIDC discovery failed: ${r.status}`);
      const meta = await r.json() as OidcMeta;
      if (!meta.authorization_endpoint) {
        throw new Error('OIDC discovery failed: authorization_endpoint missing');
      }
      const discoveredIssuer = this.normalizeHttpUrl(meta.issuer, 'OIDC discovery issuer', true);
      if (discoveredIssuer !== normalizedIssuer) {
        throw new Error('OIDC discovery failed: issuer mismatch');
      }
      const authorizationEndpoint = this.normalizeHttpUrl(
        meta.authorization_endpoint,
        'OIDC authorization endpoint',
      );
      this._meta = { ...meta, issuer: discoveredIssuer, authorization_endpoint: authorizationEndpoint };
      this._metaIssuer = normalizedIssuer;
      return this._meta;
    } finally {
      if (timeout !== undefined) window.clearTimeout(timeout);
    }
  }

  // ── PKCE helpers ─────────────────────────────────────────────────────

  private randomB64Url(bytes: number): string {
    const arr = new Uint8Array(bytes);
    crypto.getRandomValues(arr);
    return btoa(String.fromCharCode(...arr)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  }

  private async sha256B64Url(plain: string): Promise<string> {
    const encoded = new TextEncoder().encode(plain);
    const hash = await sha256Bytes(encoded);
    return btoa(String.fromCharCode(...hash)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  }

  // ── T12: PKCE Authorization redirect ────────────────────────────────

  /**
   * Returns the standard Keycloak self-registration URL for the configured
   * issuer, or an empty string if no issuer is configured.
   *
   * This URL is opened in a new tab via window.open() — there is no PKCE
   * state, no callback handling. The user fills in the keycloak-native
   * registration form and then has to click "Bei Keycloak anmelden"
   * manually to complete an OIDC login.
   *
   * Single source of truth: the network profile's `oidc.issuer`. We do
   * NOT use the public-ananta fallback here — self-registration must be
   * scoped to the same realm the user is logging in to. The button is
   * only rendered when the IdentityBridge.showRegistration gate is true,
   * which itself requires `registration_allowed` to be set by the Hub
   * (single source of truth on the backend). This service is the dumb
   * URL-builder; the visibility gate lives elsewhere.
   *
   * Returns the empty string when the issuer is missing so callers (and
   * tests) can rely on a falsy result to no-op safely.
   */
  registrationUrl(): string {
    const issuer = String(this.profiles.current?.oidc?.issuer || '')
      .trim()
      .replace(/\/$/, '');
    if (!issuer) return '';
    return `${issuer}/login-actions/registration`;
  }

  /**
   * Opens the keycloak self-registration page in a new tab. No-op when
   * no issuer is configured. Does not write to sessionStorage or localStorage
   * (no PKCE state — registration has no callback path).
   */
  registerWithKeycloak(): void {
    const url = this.registrationUrl();
    if (!url) return;
    window.open(url, '_blank');
  }

  async startLogin(redirectPath = '/', linkHub = false): Promise<void> {
    const authEndpoint = `${this.issuer.replace(/\/$/, '')}/protocol/openid-connect/auth`;
    const verifier = this.randomB64Url(48);
    const state = this.randomB64Url(16);
    const nonce = this.randomB64Url(16);
    const challenge = await this.sha256B64Url(verifier);
    const redirectUri = `${location.origin}/oidc-callback`;

    sessionStorage.setItem(SS_PKCE_KEY, JSON.stringify({ verifier, state, nonce, redirectPath, linkHub }));
    sessionStorage.setItem(SS_NONCE_KEY, nonce);

    const params = new URLSearchParams({
      client_id: this.clientId,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope: SCOPES,
      code_challenge: challenge,
      code_challenge_method: 'S256',
      state,
      nonce,
    });
    location.href = `${authEndpoint}?${params}`;
  }

  // ── T12: Handle callback after redirect ──────────────────────────────

  async handleCallback(): Promise<boolean> {
    const params = new URLSearchParams(location.search);
    const code = params.get('code');
    const state = params.get('state');
    if (!code || !state) return false;

    const stored = sessionStorage.getItem(SS_PKCE_KEY);
    if (!stored) return false;
    const { verifier, state: storedState, nonce, redirectPath, linkHub } = JSON.parse(stored) as {
      verifier: string; state: string; nonce: string; redirectPath: string; linkHub?: boolean;
    };
    if (state !== storedState) return false;
    sessionStorage.removeItem(SS_PKCE_KEY);

    const tokenEndpoint = `${this.issuer.replace(/\/$/, '')}/protocol/openid-connect/token`;
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: this.clientId,
      code,
      redirect_uri: `${location.origin}/oidc-callback`,
      code_verifier: verifier,
    });
    const r = await fetch(tokenEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!r.ok) return false;
    const tokens = await r.json();

    const idPayload = this._decodeJwt(tokens.id_token);
    if (idPayload?.nonce !== nonce) return false;

    this._sessionNonce = nonce;
    this.userAuth.setOidcAccessToken(tokens.access_token);
    await this.userAuth.setOidcRefreshToken(tokens.refresh_token ?? null);
    if (linkHub) {
      await this.linkCurrentHubIdentity(tokens.access_token);
    }
    await this.tryRestoreLinkedHubSession(tokens.access_token);
    this.router.navigateByUrl(redirectPath || '/');
    return true;
  }

  async handleBackendCallback(): Promise<boolean> {
    const params = new URLSearchParams(location.search);
    const code = params.get('oidc_code');
    if (!code) return false;
    const state = params.get('state') || '';

    const hubUrl = this.hubUrl;
    if (!hubUrl) throw new Error('Hub URL is not configured for backend OIDC token exchange');
    const exchangeParams = new URLSearchParams({ code });
    if (state) exchangeParams.set('state', state);
    const exchangeUrl = `${hubUrl.replace(/\/$/, '')}/auth/oidc/exchange?${exchangeParams}`;
    const r = await fetch(exchangeUrl, { method: 'GET', credentials: 'omit' });
    if (!r.ok) return false;
    const payload = await r.json() as any;
    const data = payload?.data || payload;
    const accessToken = String(data?.access_token || '').trim();
    if (!accessToken) return false;
    this._sessionNonce = '';
    await this.userAuth.setTokens(accessToken, data?.refresh_token || null);
    this.userAuth.setOidcAccessToken(String(data?.oidc_access_token || '').trim() || null);
    if (!window.opener) {
      await this.router.navigateByUrl(String(data?.redirect_path || '/'));
    }
    return true;
  }

  // ── Popup-PKCE login (browser equivalent of TUI loopback flow) ───────

  async startLoginPopup(issuer = this.issuer, clientId = this.clientId): Promise<void> {
    // This must happen before the first await. Otherwise browsers can discard
    // the click's user activation while discovery and PKCE are being prepared.
    const popup = window.open('about:blank', 'oidc-login', LOGIN_POPUP_FEATURES);
    if (!popup) {
      throw new OidcPopupLoginError(
        'popup_blocked',
        'Das Keycloak-Anmeldefenster wurde vom Browser blockiert. Bitte Pop-ups für diese Seite erlauben und erneut versuchen.',
      );
    }

    try {
      const normalizedIssuer = this.requireProfileIssuer(issuer);
      const normalizedClientId = String(clientId || '').trim();
      if (!normalizedClientId) {
        throw new OidcPopupLoginError(
          'configuration_missing',
          'Für das aktive Netzwerkprofil ist kein OIDC-Client konfiguriert.',
        );
      }

      let meta: OidcMeta;
      try {
        meta = await this.loadMeta(normalizedIssuer, POPUP_DISCOVERY_TIMEOUT_MS);
      } catch (error) {
        throw new OidcPopupLoginError(
          'issuer_unreachable',
          `Keycloak ist unter ${normalizedIssuer} nicht erreichbar oder liefert keine gültige OIDC-Konfiguration.`,
          { cause: error },
        );
      }
      const verifier = this.randomB64Url(48);
      const state = this.randomB64Url(16);
      const nonce = this.randomB64Url(16);
      const challenge = await this.sha256B64Url(verifier);
      const redirectUri = `${location.origin}/oidc-callback`;

      if (popup.closed) {
        throw new OidcPopupLoginError(
          'popup_closed',
          'Das Keycloak-Anmeldefenster wurde geschlossen. Bitte die Anmeldung erneut starten.',
        );
      }

      // localStorage is shared between opener and popup (unlike sessionStorage).
      localStorage.setItem(LS_POPUP_KEY, JSON.stringify({
        verifier,
        state,
        nonce,
        issuer: normalizedIssuer,
        clientId: normalizedClientId,
      }));

      const params = new URLSearchParams({
        client_id: normalizedClientId,
        redirect_uri: redirectUri,
        response_type: 'code',
        scope: SCOPES,
        code_challenge: challenge,
        code_challenge_method: 'S256',
        state,
        nonce,
      });
      const authorizationUrl = new URL(meta.authorization_endpoint);
      params.forEach((value, key) => authorizationUrl.searchParams.set(key, value));
      popup.location.replace(authorizationUrl.href);
      try { popup.focus(); } catch { /* Focusing is optional once navigation succeeded. */ }
    } catch (error) {
      localStorage.removeItem(LS_POPUP_KEY);
      if (!popup.closed) popup.close();
      if (error instanceof OidcPopupLoginError) throw error;
      throw new OidcPopupLoginError(
        'popup_start_failed',
        'Das Keycloak-Anmeldefenster konnte nicht initialisiert werden. Bitte die Anmeldung erneut starten.',
        { cause: error },
      );
    }
  }

  // Called by OidcCallbackComponent when window.opener is set
  async handleCallbackForPopup(): Promise<boolean> {
    const params = new URLSearchParams(location.search);
    const code = params.get('code');
    const state = params.get('state');
    if (!code || !state) return false;

    const stored = localStorage.getItem(LS_POPUP_KEY);
    if (!stored) return false;
    const { verifier, state: storedState, nonce, issuer, clientId } = JSON.parse(stored) as {
      verifier: string; state: string; nonce: string; issuer: string; clientId: string;
    };
    if (state !== storedState) return false;
    localStorage.removeItem(LS_POPUP_KEY);

    const tokenEndpoint = `${(issuer || this.issuer).replace(/\/$/, '')}/protocol/openid-connect/token`;
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: clientId || this.clientId,
      code,
      redirect_uri: `${location.origin}/oidc-callback`,
      code_verifier: verifier,
    });
    const r = await fetch(tokenEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!r.ok) return false;
    const tokens = await r.json();

    const idPayload = this._decodeJwt(tokens.id_token);
    if (idPayload?.nonce && idPayload.nonce !== nonce) return false;

    // Write nonce to localStorage so parent window can read it via storage event
    localStorage.setItem('oidc.popup.nonce', nonce);
    this._sessionNonce = nonce;
    // Writing the OIDC access token fires a storage event in the parent.
    this.userAuth.setOidcAccessToken(tokens.access_token);
    await this.userAuth.setOidcRefreshToken(tokens.refresh_token ?? null);
    await this.tryRestoreLinkedHubSession(tokens.access_token);
    return true;
  }

// ── T13: Silent token refresh via OIDC token endpoint ────────────────

  /**
   * Refresh using the encrypted OIDC RT from storage.
   * Returns true on success, false on failure (logs nothing — caller decides).
   */
  async refreshFromStorage(): Promise<boolean> {
    const refreshToken = await this.userAuth.getOidcRefreshToken();
    if (!refreshToken) return false;
    try {
      const tokenEndpoint = `${this.issuer.replace(/\/$/, '')}/protocol/openid-connect/token`;
      const body = new URLSearchParams({
        grant_type: 'refresh_token',
        client_id: this.clientId,
        refresh_token: refreshToken,
      });
      const r = await fetch(tokenEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
      });
      if (!r.ok) {
        this.userAuth.setOidcAccessToken(null);
        await this.userAuth.setOidcRefreshToken(null);
        return false;
      }
      const tokens = await r.json();
      this.userAuth.setOidcAccessToken(tokens.access_token);
      await this.userAuth.setOidcRefreshToken(tokens.refresh_token ?? refreshToken);
      await this.tryRestoreLinkedHubSession(tokens.access_token);
      return true;
    } catch {
      return false;
    }
  }

  async silentRefresh(): Promise<boolean> {
    return this.refreshFromStorage();
  }

  // ── T14: Keycloak end-session ────────────────────────────────────────

  async logout(): Promise<void> {
    this.userAuth.setOidcAccessToken(null);
    await this.userAuth.setOidcRefreshToken(null);
    this._sessionNonce = '';
    try {
      const meta = await this.loadMeta();
      const params = new URLSearchParams({
        client_id: this.clientId,
        post_logout_redirect_uri: `${location.origin}/login`,
      });
      location.href = `${meta.end_session_endpoint}?${params}`;
    } catch {
      this.router.navigate(['/login']);
    }
  }

  private async tryRestoreLinkedHubSession(oidcAccessToken: string): Promise<void> {
    const oidc = this.profiles.current.oidc;
    const linkEnabled = oidc?.hub_link_enabled === true || oidc?.bridge_active === true;
    const hubUrl = this.hubUrl;
    if (!linkEnabled || !hubUrl) return;
    try {
      const response = await fetch(`${hubUrl.replace(/\/$/, '')}/auth/oidc/exchange`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ oidc_access_token: oidcAccessToken }),
      });
      if (!response.ok) return;
      const payload = await response.json() as any;
      const data = payload?.data ?? payload;
      if (data?.access_token) {
        await this.userAuth.setTokens(data.access_token, data.refresh_token);
      }
    } catch {
      // Pair login remains valid when the optional Hub exchange is unavailable.
    }
  }

  private async linkCurrentHubIdentity(oidcAccessToken: string): Promise<void> {
    const hubUrl = this.hubUrl;
    const hubToken = this.userAuth.token;
    if (!hubUrl || !hubToken) throw new Error('Hub login required before account linking');
    const response = await fetch(`${hubUrl.replace(/\/$/, '')}/auth/oidc/link`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${hubToken}`,
      },
      body: JSON.stringify({ oidc_access_token: oidcAccessToken }),
    });
    if (!response.ok) {
      throw new Error(`Account linking failed: HTTP ${response.status}`);
    }
  }

  // ── T15: Device Flow ─────────────────────────────────────────────────

  async startDeviceFlow(): Promise<DeviceAuthResponse> {
    const meta = await this.loadMeta();
    const endpoint = meta.device_authorization_endpoint ??
      `${this.issuer}/protocol/openid-connect/auth/device`;
    const body = new URLSearchParams({ client_id: this.clientId, scope: SCOPES });
    const r = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!r.ok) throw new Error(`Device flow start failed: ${r.status}`);
    return r.json() as Promise<DeviceAuthResponse>;
  }

  async pollDeviceToken(deviceCode: string, intervalSec: number): Promise<boolean> {
    const meta = await this.loadMeta();
    const body = new URLSearchParams({
      grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
      client_id: this.clientId,
      device_code: deviceCode,
    });
    const r = await fetch(meta.token_endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (r.status === 400) {
      const err = await r.json();
      if (err.error === 'authorization_pending' || err.error === 'slow_down') return false;
      throw new Error(err.error);
    }
    if (!r.ok) return false;
    const tokens = await r.json();
    const nonce = sessionStorage.getItem(SS_NONCE_KEY) ?? this.randomB64Url(16);
    this._sessionNonce = nonce;
    this.userAuth.setOidcAccessToken(tokens.access_token);
    await this.userAuth.setOidcRefreshToken(tokens.refresh_token ?? null);
    await this.tryRestoreLinkedHubSession(tokens.access_token);
    return true;
  }

  // ── Helpers ──────────────────────────────────────────────────────────

  private requireProfileIssuer(value: string): string {
    try {
      return this.normalizeHttpUrl(value, 'OIDC issuer', true);
    } catch (error) {
      const missing = !String(value || '').trim();
      throw new OidcPopupLoginError(
        'configuration_missing',
        missing
          ? 'Für das aktive Netzwerkprofil ist kein OIDC-Issuer konfiguriert.'
          : 'Der OIDC-Issuer im aktiven Netzwerkprofil ist ungültig.',
        { cause: error },
      );
    }
  }

  private normalizeHttpUrl(value: string, label: string, isIssuer = false): string {
    const candidate = String(value || '').trim().replace(/\/+$/, '');
    if (!candidate) throw new Error(`${label} is missing`);
    const parsed = new URL(candidate);
    const hostname = parsed.hostname.toLowerCase();
    const localhost = hostname === 'localhost'
      || hostname === '127.0.0.1'
      || hostname === '[::1]'
      || hostname.endsWith('.localhost');
    if (parsed.protocol !== 'https:' && !(parsed.protocol === 'http:' && localhost)) {
      throw new Error(`${label} must use HTTPS or localhost HTTP`);
    }
    if (parsed.username || parsed.password || (isIssuer && (parsed.search || parsed.hash))) {
      throw new Error(`${label} contains unsupported URL components`);
    }
    return parsed.href.replace(/\/$/, '');
  }

  private _decodeJwt(token: string): any {
    try {
      const parts = token.split('.');
      if (parts.length !== 3) return null;
      const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      return JSON.parse(atob(payload + '='.repeat((4 - payload.length % 4) % 4)));
    } catch { return null; }
  }

  private randomB64UrlSync(bytes: number): string {
    return this.randomB64Url(bytes);
  }
}
