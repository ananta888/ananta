/**
 * T12 / T13 / T14 / T15 / T16: OIDC PKCE + Device Flow + Refresh + Logout + Nonce
 * Issuer: keycloak.ananta.de/realms/ananta-e2e  client_id: ananta-tui
 */
import { Injectable, inject } from '@angular/core';
import { Router } from '@angular/router';
import { firstValueFrom, map } from 'rxjs';
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
  private dir = inject(AgentDirectoryService);
  private profiles = inject(NetworkProfileService);
  private router = inject(Router);

  private _meta: OidcMeta | null = null;
  private _sessionNonce = '';

  readonly loggedIn$ = this.userAuth.token$.pipe(map(t => !!t));

  get sessionNonce(): string { return this._sessionNonce; }
  get hasNonce(): boolean { return !!this._sessionNonce; }

  get issuer(): string {
    return this.profiles.current.oidc?.issuer || PUBLIC_OIDC_ISSUER;
  }

  get clientId(): string {
    return this.profiles.current.oidc?.client_id || PUBLIC_OIDC_CLIENT_ID;
  }

  private get hubUrl(): string {
    return this.dir.list().find((agent) => agent.role === 'hub')?.url || '';
  }

  private get usesBackendOidcBroker(): boolean {
    return this.profiles.current.profile_id === 'public-ananta';
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
        } else if (e.key === 'oidc.popup.nonce' && e.newValue) {
          this._sessionNonce = e.newValue;
          localStorage.removeItem('oidc.popup.nonce');
        }
      });
    }
  }

  // ── Discovery ────────────────────────────────────────────────────────

  private async loadMeta(issuer = this.issuer): Promise<OidcMeta> {
    if (issuer === this.issuer && this._meta) return this._meta;
    const r = await fetch(`${issuer.replace(/\/$/, '')}/.well-known/openid-configuration`);
    if (!r.ok) throw new Error(`OIDC discovery failed: ${r.status}`);
    const meta = await r.json() as OidcMeta;
    if (issuer === this.issuer) this._meta = meta;
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
    const subtle = globalThis.crypto?.subtle;
    if (subtle) {
      const hash = await subtle.digest('SHA-256', encoded);
      return btoa(String.fromCharCode(...new Uint8Array(hash))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
    }
    const hash = this.sha256Fallback(encoded);
    return btoa(String.fromCharCode(...new Uint8Array(hash))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  }

  // ── T12: PKCE Authorization redirect ────────────────────────────────

  async startLogin(redirectPath = '/'): Promise<void> {
    if (this.usesBackendOidcBroker) {
      const hubUrl = this.hubUrl;
      if (!hubUrl) throw new Error('Hub URL is not configured for backend OIDC login');
      const params = new URLSearchParams({ redirect_path: redirectPath || '/' });
      location.href = `${hubUrl.replace(/\/$/, '')}/auth/oidc/login?${params}`;
      return;
    }

    const authEndpoint = `${this.issuer.replace(/\/$/, '')}/protocol/openid-connect/auth`;
    const verifier = this.randomB64Url(48);
    const state = this.randomB64Url(16);
    const nonce = this.randomB64Url(16);
    const challenge = await this.sha256B64Url(verifier);
    const redirectUri = `${location.origin}/oidc-callback`;

    sessionStorage.setItem(SS_PKCE_KEY, JSON.stringify({ verifier, state, nonce, redirectPath }));
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
    const { verifier, state: storedState, nonce, redirectPath } = JSON.parse(stored) as {
      verifier: string; state: string; nonce: string; redirectPath: string;
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

    const isPublicProfile = this.profiles.current.profile_id === 'public-ananta';
    if (isPublicProfile) {
      const hubUrl = this.hubUrl;
      if (!hubUrl) throw new Error('Hub URL is not configured for public OIDC token exchange');
      const exchangeUrl = `${hubUrl.replace(/\/$/, '')}/auth/oidc/exchange`;
      const exchangeResponse = await fetch(exchangeUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          id_token: tokens.id_token,
          oidc_access_token: tokens.access_token,
          redirect_path: redirectPath || '/',
        }),
      });
      if (!exchangeResponse.ok) return false;
      const exchangePayload = await exchangeResponse.json() as any;
      const exchangeData = exchangePayload?.data || exchangePayload;
      const appAccessToken = String(exchangeData?.access_token || '').trim();
      if (!appAccessToken) return false;
      this.userAuth.setTokens(appAccessToken, exchangeData?.refresh_token || null);
      this.userAuth.setOidcAccessToken(String(exchangeData?.oidc_access_token || tokens.access_token || '').trim());
      this.router.navigateByUrl(String(exchangeData?.redirect_path || redirectPath || '/'));
      return true;
    }

    this.userAuth.setTokens(tokens.access_token, tokens.refresh_token);
    this.router.navigateByUrl(redirectPath || '/');
    return true;
  }

  async handleBackendCallback(): Promise<boolean> {
    const params = new URLSearchParams(location.search);
    const code = params.get('oidc_code') || params.get('code');
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
    this.userAuth.setTokens(accessToken, data?.refresh_token || null);
    this.userAuth.setOidcAccessToken(String(data?.oidc_access_token || '').trim() || null);
    this.router.navigateByUrl(String(data?.redirect_path || '/'));
    return true;
  }

  // ── Popup-PKCE login (browser equivalent of TUI loopback flow) ───────

  async startLoginPopup(issuer = this.issuer, clientId = this.clientId): Promise<void> {
    const authEndpoint = `${issuer.replace(/\/$/, '')}/protocol/openid-connect/auth`;
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
      `${authEndpoint}?${params}`,
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
    // setTokens writes to localStorage → fires storage event in parent window
    this.userAuth.setTokens(tokens.access_token, tokens.refresh_token);
    this.userAuth.setOidcAccessToken(tokens.access_token);
    return true;
  }

// ── T13: Silent token refresh via OIDC token endpoint ────────────────

  /**
   * Refresh using the encrypted OIDC RT from storage.
   * Returns true on success, false on failure (logs nothing — caller decides).
   */
  async refreshFromStorage(): Promise<boolean> {
    if (this.usesBackendOidcBroker) {
      try {
        await firstValueFrom(this.userAuth.refreshToken());
        return true;
      } catch {
        return false;
      }
    }
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
      if (!r.ok) { this.userAuth.logout(); return false; }
      const tokens = await r.json();
      this.userAuth.setOidcAccessToken(tokens.access_token);
      await this.userAuth.setOidcRefreshToken(tokens.refresh_token ?? refreshToken);
      return true;
    } catch {
      return false;
    }
  }

  async silentRefresh(): Promise<boolean> {
    if (this.usesBackendOidcBroker) {
      try {
        await firstValueFrom(this.userAuth.refreshToken());
        return true;
      } catch {
        return false;
      }
    }

    const refreshToken = this.userAuth.refreshTokenValue;
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
      if (!r.ok) { this.userAuth.logout(); return false; }
      const tokens = await r.json();
      this.userAuth.setTokens(tokens.access_token, tokens.refresh_token ?? refreshToken);
      this.userAuth.setOidcAccessToken(tokens.access_token);
      return true;
    } catch {
      return false;
    }
  }

  // ── T14: Keycloak end-session ────────────────────────────────────────

  async logout(): Promise<void> {
    if (this.usesBackendOidcBroker) {
      this.userAuth.logout();
      this._sessionNonce = '';
      this.router.navigate(['/login']);
      return;
    }

    const token = this.userAuth.token;
    this.userAuth.logout();
    this._sessionNonce = '';
    try {
      const meta = await this.loadMeta();
      const params = new URLSearchParams({
        client_id: this.clientId,
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
    this.userAuth.setTokens(tokens.access_token, tokens.refresh_token);
    this.userAuth.setOidcAccessToken(tokens.access_token);
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

  private sha256Fallback(message: Uint8Array): Uint8Array {
    const K = new Uint32Array([
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
      0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
      0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ]);
    const H = new Uint32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    const bitLen = message.length * 8;
    const paddedLength = (((message.length + 9 + 63) >> 6) << 6);
    const padded = new Uint8Array(paddedLength);
    padded.set(message);
    padded[message.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 4, bitLen >>> 0, false);
    view.setUint32(padded.length - 8, Math.floor(bitLen / 0x100000000), false);

    const w = new Uint32Array(64);
    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let i = 0; i < 16; i += 1) {
        w[i] = view.getUint32(offset + i * 4, false);
      }
      for (let i = 16; i < 64; i += 1) {
        const s0 = this._rotr(w[i - 15], 7) ^ this._rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = this._rotr(w[i - 2], 17) ^ this._rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = H;
      for (let i = 0; i < 64; i += 1) {
        const S1 = this._rotr(e, 6) ^ this._rotr(e, 11) ^ this._rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const temp1 = (h + S1 + ch + K[i] + w[i]) >>> 0;
        const S0 = this._rotr(a, 2) ^ this._rotr(a, 13) ^ this._rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const temp2 = (S0 + maj) >>> 0;
        h = g;
        g = f;
        f = e;
        e = (d + temp1) >>> 0;
        d = c;
        c = b;
        b = a;
        a = (temp1 + temp2) >>> 0;
      }
      H[0] = (H[0] + a) >>> 0;
      H[1] = (H[1] + b) >>> 0;
      H[2] = (H[2] + c) >>> 0;
      H[3] = (H[3] + d) >>> 0;
      H[4] = (H[4] + e) >>> 0;
      H[5] = (H[5] + f) >>> 0;
      H[6] = (H[6] + g) >>> 0;
      H[7] = (H[7] + h) >>> 0;
    }

    const out = new Uint8Array(32);
    for (let i = 0; i < H.length; i += 1) {
      out[i * 4] = (H[i] >>> 24) & 0xff;
      out[i * 4 + 1] = (H[i] >>> 16) & 0xff;
      out[i * 4 + 2] = (H[i] >>> 8) & 0xff;
      out[i * 4 + 3] = H[i] & 0xff;
    }
    return out;
  }

  private _rotr(value: number, shift: number): number {
    return (value >>> shift) | (value << (32 - shift));
  }

  private randomB64UrlSync(bytes: number): string {
    return this.randomB64Url(bytes);
  }
}
