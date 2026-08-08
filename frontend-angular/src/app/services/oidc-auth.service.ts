/** T12 / T13 / T14 / T15 / T16: OIDC PKCE + Device Flow + Refresh + Logout + Nonce. */
import { Injectable, OnDestroy, inject } from '@angular/core';
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
import {
  OidcPopupCoordinator,
  OidcPopupCoordinatorError,
  type OidcPopupParentSession,
} from './oidc-popup-coordinator.service';

const SCOPES = 'openid profile email';
const SS_PKCE_KEY = 'oidc.pkce';       // sessionStorage
const SS_NONCE_KEY = 'oidc.nonce';
const LOGIN_POPUP_FEATURES = 'width=560,height=680,left=200,top=80';
const POPUP_DISCOVERY_TIMEOUT_MS = 10_000;
const POPUP_TOKEN_EXCHANGE_TIMEOUT_MS = 45_000;

export type OidcPopupLoginFailure =
  | 'popup_blocked'
  | 'configuration_missing'
  | 'popup_closed'
  | 'popup_timeout'
  | 'popup_communication_failed'
  | 'authorization_denied'
  | 'callback_invalid'
  | 'token_exchange_failed'
  | 'token_exchange_timeout'
  | 'token_endpoint_unreachable'
  | 'nonce_mismatch'
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
export class OidcAuthService implements OnDestroy {
  private userAuth = inject(UserAuthService);
  private dir = inject(AgentDirectoryService);
  private profiles = inject(NetworkProfileService);
  private router = inject(Router);
  private popupCoordinator = inject(OidcPopupCoordinator);

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

  private readonly onStorage = (event: StorageEvent) => {
    if (this.isPopupCallback()) return;
    if (event.key === 'ananta.user.token' && event.newValue) {
      const refresh = localStorage.getItem('ananta.user.refresh_token') ?? undefined;
      void this.userAuth.setTokens(event.newValue, refresh);
    } else if (event.key === 'ananta.oidc.access_token') {
      this.userAuth.setOidcAccessToken(event.newValue);
      if (event.newValue) {
        void this.tryRestoreLinkedHubSession(event.newValue);
      }
    }
  };

  constructor() {
    // Legacy cross-tab and backend-broker synchronization. Popup-PKCE no
    // longer depends on this event; its parent commits the tokens directly.
    window.addEventListener('storage', this.onStorage);
  }

  ngOnDestroy(): void {
    window.removeEventListener('storage', this.onStorage);
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
      if (!meta.authorization_endpoint || !meta.token_endpoint) {
        throw new Error('OIDC discovery failed: required endpoint missing');
      }
      const discoveredIssuer = this.normalizeHttpUrl(meta.issuer, 'OIDC discovery issuer', true);
      if (discoveredIssuer !== normalizedIssuer) {
        throw new Error('OIDC discovery failed: issuer mismatch');
      }
      const authorizationEndpoint = this.normalizeHttpUrl(
        meta.authorization_endpoint,
        'OIDC authorization endpoint',
      );
      const tokenEndpoint = this.normalizeHttpUrl(meta.token_endpoint, 'OIDC token endpoint');
      this._meta = {
        ...meta,
        issuer: discoveredIssuer,
        authorization_endpoint: authorizationEndpoint,
        token_endpoint: tokenEndpoint,
      };
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
    // Clear sensitive PKCE material left by the pre-coordinator implementation.
    this.removeLocalStorageItem('oidc.pkce.popup');
    const verifier = this.randomB64Url(48);
    const state = this.popupCoordinator.createState(this.randomB64Url(24));
    const nonce = this.randomB64Url(24);

    // This must happen before the first await. Otherwise browsers can discard
    // the click's user activation while discovery and PKCE are being prepared.
    const popup = window.open('about:blank', `oidc-login-${state}`, LOGIN_POPUP_FEATURES);
    if (!popup) {
      throw new OidcPopupLoginError(
        'popup_blocked',
        'Das Keycloak-Anmeldefenster wurde vom Browser blockiert. Bitte Pop-ups für diese Seite erlauben und erneut versuchen.',
      );
    }

    let parentSession: OidcPopupParentSession | null = null;
    let callbackReceived = false;
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
      const challenge = await this.sha256B64Url(verifier);
      const redirectUri = `${location.origin}/oidc-callback`;

      if (popup.closed) {
        throw new OidcPopupLoginError(
          'popup_closed',
          'Das Keycloak-Anmeldefenster wurde geschlossen. Bitte die Anmeldung erneut starten.',
        );
      }

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

      // The parent owns verifier, nonce, endpoint and token persistence. The
      // popup only returns the one-time code over a state-bound same-origin
      // channel, so no token or PKCE secret crosses the window boundary.
      parentSession = this.popupCoordinator.beginParentSession(state, popup);
      popup.location.replace(authorizationUrl.href);
      try { popup.focus(); } catch { /* Focusing is optional once navigation succeeded. */ }

      const authorization = await parentSession.result;
      callbackReceived = true;
      if (authorization.kind === 'error') {
        throw this.authorizationError(authorization.errorCode);
      }

      await this.exchangePopupAuthorizationCode({
        tokenEndpoint: meta.token_endpoint,
        clientId: normalizedClientId,
        code: authorization.code,
        redirectUri,
        verifier,
        nonce,
      });
      parentSession.acknowledge({ ok: true });
    } catch (error) {
      const loginError = this.normalizePopupError(error, callbackReceived);
      if (callbackReceived && parentSession) {
        parentSession.acknowledge({
          ok: false,
          errorCode: loginError.code,
          message: loginError.message,
        });
      } else {
        parentSession?.dispose();
        this.closePopup(popup);
      }
      throw loginError;
    } finally {
      parentSession?.dispose();
      this.removeLocalStorageItem('oidc.pkce.popup');
    }
  }

  isPopupCallback(search = location.search): boolean {
    return this.popupCoordinator.isPopupCallback(search);
  }

  // Called by OidcCallbackComponent for a state-prefixed popup callback.
  async handleCallbackForPopup(): Promise<boolean> {
    await this.popupCoordinator.relayCurrentCallback();
    return true;
  }

  private async exchangePopupAuthorizationCode(input: {
    tokenEndpoint: string;
    clientId: string;
    code: string;
    redirectUri: string;
    verifier: string;
    nonce: string;
  }): Promise<void> {
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: input.clientId,
      code: input.code,
      redirect_uri: input.redirectUri,
      code_verifier: input.verifier,
    });

    let response: Response;
    const controller = new AbortController();
    const timeoutHandle = window.setTimeout(
      () => controller.abort(),
      POPUP_TOKEN_EXCHANGE_TIMEOUT_MS,
    );
    try {
      response = await fetch(input.tokenEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
        signal: controller.signal,
      });
    } catch (error) {
      if (controller.signal.aborted || this.isAbortError(error)) {
        throw new OidcPopupLoginError(
          'token_exchange_timeout',
          'Keycloak hat den Anmeldecode nicht rechtzeitig eingelöst. Bitte eine neue Anmeldung starten.',
          { cause: error },
        );
      }
      throw new OidcPopupLoginError(
        'token_endpoint_unreachable',
        'Der Browser hat vom Keycloak-Token-Endpunkt keine lesbare Antwort erhalten. Bitte Netzwerk und Keycloak-Verfügbarkeit prüfen und die Anmeldung neu starten.',
        { cause: error },
      );
    } finally {
      window.clearTimeout(timeoutHandle);
    }

    if (!response.ok) {
      throw new OidcPopupLoginError(
        'token_exchange_failed',
        'Keycloak hat den Anmeldecode abgelehnt oder er ist abgelaufen. Bitte erneut anmelden.',
      );
    }

    let tokens: Record<string, unknown>;
    try {
      tokens = await response.json() as Record<string, unknown>;
    } catch (error) {
      throw new OidcPopupLoginError(
        'token_exchange_failed',
        'Keycloak hat keine gültige Token-Antwort geliefert.',
        { cause: error },
      );
    }

    const accessToken = typeof tokens['access_token'] === 'string' ? tokens['access_token'] : '';
    const idToken = typeof tokens['id_token'] === 'string' ? tokens['id_token'] : '';
    const refreshToken = typeof tokens['refresh_token'] === 'string' ? tokens['refresh_token'] : null;
    if (!accessToken || !idToken) {
      throw new OidcPopupLoginError(
        'token_exchange_failed',
        'Keycloak hat die erforderlichen OIDC-Tokens nicht geliefert.',
      );
    }

    const idPayload = this._decodeJwt(idToken);
    if (!idPayload || idPayload.nonce !== input.nonce) {
      throw new OidcPopupLoginError(
        'nonce_mismatch',
        'Die Keycloak-Antwort gehört nicht zur gestarteten Anmeldung.',
      );
    }

    // Persist only after code and nonce validation. The parent is the sole
    // writer; no access or refresh token is sent over the popup transport.
    await this.userAuth.setOidcRefreshToken(refreshToken);
    this._sessionNonce = input.nonce;
    this.userAuth.setOidcAccessToken(accessToken);
    // Pair/OIDC login is complete here. Optional Hub linking must not delay
    // the popup acknowledgement or turn a healthy Pair login into a timeout.
    void this.tryRestoreLinkedHubSession(accessToken);
  }

  private isAbortError(error: unknown): boolean {
    return error instanceof DOMException
      ? error.name === 'AbortError'
      : error instanceof Error && error.name === 'AbortError';
  }

  private authorizationError(errorCode: string): OidcPopupLoginError {
    if (errorCode === 'access_denied') {
      return new OidcPopupLoginError(
        'authorization_denied',
        'Die Keycloak-Anmeldung wurde abgebrochen oder abgelehnt.',
      );
    }
    if (errorCode === 'communication_unavailable') {
      return new OidcPopupLoginError(
        'popup_communication_failed',
        'Das Callback-Fenster konnte das Hauptfenster nicht sicher erreichen.',
      );
    }
    return new OidcPopupLoginError(
      'callback_invalid',
      'Keycloak konnte die Popup-Anmeldung nicht abschließen. Bitte erneut anmelden.',
    );
  }

  private normalizePopupError(error: unknown, callbackReceived: boolean): OidcPopupLoginError {
    if (error instanceof OidcPopupLoginError) return error;
    if (error instanceof OidcPopupCoordinatorError) {
      if (error.code === 'popup_timeout') {
        return new OidcPopupLoginError('popup_timeout', error.message, { cause: error });
      }
      if (error.code === 'communication_unavailable') {
        return new OidcPopupLoginError('popup_communication_failed', error.message, { cause: error });
      }
      return new OidcPopupLoginError('callback_invalid', error.message, { cause: error });
    }
    return new OidcPopupLoginError(
      callbackReceived ? 'token_exchange_failed' : 'popup_start_failed',
      callbackReceived
        ? 'Die Keycloak-Anmeldung konnte nicht abgeschlossen werden. Bitte erneut anmelden.'
        : 'Das Keycloak-Anmeldefenster konnte nicht initialisiert werden. Bitte die Anmeldung erneut starten.',
      error instanceof Error ? { cause: error } : undefined,
    );
  }

  private closePopup(popup: Window): void {
    try {
      if (!popup.closed) popup.close();
    } catch {
      try { popup.close(); } catch { /* The browser owns a severed popup proxy. */ }
    }
  }

  private removeLocalStorageItem(key: string): void {
    try { localStorage.removeItem(key); } catch { /* Cleanup is best-effort. */ }
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
