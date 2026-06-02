/**
 * T12 / T13 / T14 / T15 / T16: OIDC PKCE + Device Flow + Refresh + Logout + Nonce
 * Issuer: keycloak.ananta.de/realms/ananta-e2e  client_id: ananta-tui
 */
import { Injectable, inject } from '@angular/core';
import { Router } from '@angular/router';
import { map } from 'rxjs';
import { UserAuthService } from './user-auth.service';

const ISSUER = 'https://keycloak.ananta.de/realms/ananta-e2e';
const CLIENT_ID = 'ananta-tui';
const SCOPES = 'openid profile email';
const SS_PKCE_KEY = 'oidc.pkce';       // sessionStorage
const SS_NONCE_KEY = 'oidc.nonce';
const LS_POPUP_KEY = 'oidc.pkce.popup'; // localStorage — shared with popup window

interface OidcMeta {
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
  private router = inject(Router);

  private _meta: OidcMeta | null = null;
  private _sessionNonce = '';

  readonly loggedIn$ = this.userAuth.token$.pipe(map(t => !!t));

  get sessionNonce(): string { return this._sessionNonce; }
  get hasNonce(): boolean { return !!this._sessionNonce; }

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
        } else if (e.key === 'oidc.popup.nonce' && e.newValue) {
          this._sessionNonce = e.newValue;
          localStorage.removeItem('oidc.popup.nonce');
        }
      });
    }
  }

  // ── Discovery ────────────────────────────────────────────────────────

  private async loadMeta(issuer = ISSUER): Promise<OidcMeta> {
    if (issuer === ISSUER && this._meta) return this._meta;
    const r = await fetch(`${issuer.replace(/\/$/, '')}/.well-known/openid-configuration`);
    if (!r.ok) throw new Error(`OIDC discovery failed: ${r.status}`);
    const meta = await r.json() as OidcMeta;
    if (issuer === ISSUER) this._meta = meta;
    return meta;
  }

  // ── PKCE helpers ─────────────────────────────────────────────────────

  private randomB64Url(bytes: number): string {
    const arr = new Uint8Array(bytes);
    crypto.getRandomValues(arr);
    return btoa(String.fromCharCode(...arr)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  }

  private async sha256B64Url(plain: string): Promise<string> {
    const encoded = new TextEncoder().encode(plain);
    const hash = await crypto.subtle.digest('SHA-256', encoded);
    return btoa(String.fromCharCode(...new Uint8Array(hash))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  }

  // ── T12: PKCE Authorization redirect ────────────────────────────────

  async startLogin(redirectPath = '/'): Promise<void> {
    const meta = await this.loadMeta(ISSUER);
    const verifier = this.randomB64Url(48);
    const state = this.randomB64Url(16);
    const nonce = this.randomB64Url(16);
    const challenge = await this.sha256B64Url(verifier);
    const redirectUri = `${location.origin}/oidc-callback`;

    sessionStorage.setItem(SS_PKCE_KEY, JSON.stringify({ verifier, state, nonce, redirectPath }));
    sessionStorage.setItem(SS_NONCE_KEY, nonce);

    const params = new URLSearchParams({
      client_id: CLIENT_ID,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope: SCOPES,
      code_challenge: challenge,
      code_challenge_method: 'S256',
      state,
      nonce,
    });
    location.href = `${meta.authorization_endpoint}?${params}`;
  }

  // ── T12: Handle callback after redirect ──────────────────────────────

  async handleCallback(): Promise<boolean> {
    const params = new URLSearchParams(location.search);
    const code = params.get('code');
    const state = params.get('state');
    if (!code || !state) return false;

    const stored = sessionStorage.getItem(SS_PKCE_KEY);
    if (!stored) return false;
    const { verifier, state: storedState, nonce, redirectPath } = JSON.parse(stored) as {
      verifier: string; state: string; nonce: string; redirectPath: string;
    };
    if (state !== storedState) return false;
    sessionStorage.removeItem(SS_PKCE_KEY);

    const meta = await this.loadMeta(ISSUER);
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: CLIENT_ID,
      code,
      redirect_uri: `${location.origin}/oidc-callback`,
      code_verifier: verifier,
    });
    const r = await fetch(meta.token_endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!r.ok) return false;
    const tokens = await r.json();

    const idPayload = this._decodeJwt(tokens.id_token);
    if (idPayload?.nonce !== nonce) return false;

    this._sessionNonce = nonce;
    this.userAuth.setTokens(tokens.access_token, tokens.refresh_token);
    this.router.navigateByUrl(redirectPath || '/');
    return true;
  }

  // ── Popup-PKCE login (browser equivalent of TUI loopback flow) ───────

  async startLoginPopup(issuer = ISSUER, clientId = CLIENT_ID): Promise<void> {
    const meta = await this.loadMeta(issuer);
    const verifier = this.randomB64Url(48);
    const state = this.randomB64Url(16);
    const nonce = this.randomB64Url(16);
    const challenge = await this.sha256B64Url(verifier);
    const redirectUri = `${location.origin}/oidc-callback`;

    // localStorage is shared between opener and popup (unlike sessionStorage)
    localStorage.setItem(LS_POPUP_KEY, JSON.stringify({ verifier, state, nonce, issuer, clientId }));

    const params = new URLSearchParams({
      client_id: clientId,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope: SCOPES,
      code_challenge: challenge,
      code_challenge_method: 'S256',
      state,
      nonce,
    });
    window.open(
      `${meta.authorization_endpoint}?${params}`,
      'oidc-login',
      'width=560,height=680,left=200,top=80',
    );
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

    const meta = await this.loadMeta(issuer || ISSUER);
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: clientId || CLIENT_ID,
      code,
      redirect_uri: `${location.origin}/oidc-callback`,
      code_verifier: verifier,
    });
    const r = await fetch(meta.token_endpoint, {
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
    // setTokens writes to localStorage → fires storage event in parent window
    this.userAuth.setTokens(tokens.access_token, tokens.refresh_token);
    return true;
  }

  // ── T13: Silent token refresh via OIDC token endpoint ───────────────

  async silentRefresh(): Promise<boolean> {
    const refreshToken = this.userAuth.refreshTokenValue;
    if (!refreshToken) return false;
    try {
      const meta = await this.loadMeta(ISSUER);
      const body = new URLSearchParams({
        grant_type: 'refresh_token',
        client_id: CLIENT_ID,
        refresh_token: refreshToken,
      });
      const r = await fetch(meta.token_endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
      });
      if (!r.ok) { this.userAuth.logout(); return false; }
      const tokens = await r.json();
      this.userAuth.setTokens(tokens.access_token, tokens.refresh_token ?? refreshToken);
      return true;
    } catch {
      return false;
    }
  }

  // ── T14: Keycloak end-session ────────────────────────────────────────

  async logout(): Promise<void> {
    const token = this.userAuth.token;
    this.userAuth.logout();
    this._sessionNonce = '';
    try {
      const meta = await this.loadMeta(ISSUER);
      const params = new URLSearchParams({
        client_id: CLIENT_ID,
        post_logout_redirect_uri: `${location.origin}/login`,
      });
      if (token) params.set('id_token_hint', token);
      location.href = `${meta.end_session_endpoint}?${params}`;
    } catch {
      this.router.navigate(['/login']);
    }
  }

  // ── T15: Device Flow ─────────────────────────────────────────────────

  async startDeviceFlow(): Promise<DeviceAuthResponse> {
    const meta = await this.loadMeta(ISSUER);
    const endpoint = meta.device_authorization_endpoint ??
      `${ISSUER}/protocol/openid-connect/auth/device`;
    const body = new URLSearchParams({ client_id: CLIENT_ID, scope: SCOPES });
    const r = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!r.ok) throw new Error(`Device flow start failed: ${r.status}`);
    return r.json() as Promise<DeviceAuthResponse>;
  }

  async pollDeviceToken(deviceCode: string, intervalSec: number): Promise<boolean> {
    const meta = await this.loadMeta(ISSUER);
    const body = new URLSearchParams({
      grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
      client_id: CLIENT_ID,
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
    this.userAuth.setTokens(tokens.access_token, tokens.refresh_token);
    return true;
  }

  // ── Helpers ──────────────────────────────────────────────────────────

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
