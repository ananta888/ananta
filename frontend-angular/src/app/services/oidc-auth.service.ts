/** T12 / T13 / T14 / T15 / T16: OIDC PKCE + Device Flow + Refresh + Logout + Nonce. */
import { Injectable, OnDestroy, inject } from '@angular/core';
import { sha256Bytes } from '../shared/crypto/sha256';
import { Router } from '@angular/router';
import { distinctUntilChanged, map } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';
import { NetworkProfileService } from './network-profile.service';
import {
  PairPublicAuthorityPolicy,
  type PublicOidcLoginAuthority,
} from './pair-public-authority.policy';
import {
  UserAuthService,
  type OidcSessionCommitResult,
} from './user-auth.service';
import {
  PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
  PUBLIC_OIDC_CLIENT_ID,
  PUBLIC_OIDC_DEVICE_AUTHORIZATION_ENDPOINT,
  PUBLIC_OIDC_END_SESSION_ENDPOINT,
  PUBLIC_OIDC_ISSUER,
  PUBLIC_OIDC_TOKEN_ENDPOINT,
} from './public-ananta-endpoints';
import {
  OidcPopupCoordinator,
  OidcPopupCoordinatorError,
  type OidcPopupParentSession,
} from './oidc-popup-coordinator.service';
import {
  inspectJwtAccessToken,
  isJwtAccessTokenCurrent,
} from './identity/jwt-access-token';
import { IDENTITY_STORAGE_LAYOUT } from './identity/identity-storage-layout';
import { OidcRefreshLock } from './oidc-refresh-lock.service';
import { decodeOidcJwt } from './oidc-jwt';

const SCOPES = 'openid profile email';
const SS_PKCE_KEY = 'oidc.pkce';       // sessionStorage
const SS_NONCE_KEY = 'oidc.nonce';
const REFRESH_AUTHORITY_KEY = IDENTITY_STORAGE_LAYOUT.oidc.refreshAuthority.key;
const LOGIN_POPUP_FEATURES = 'width=560,height=680,left=200,top=80';
const POPUP_DISCOVERY_TIMEOUT_MS = 10_000;
const POPUP_TOKEN_EXCHANGE_TIMEOUT_MS = 45_000;
const REFRESH_TOKEN_EXCHANGE_TIMEOUT_MS = 15_000;

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

interface DeviceFlowAuthorityBinding {
  readonly tokenEndpoint: string;
  readonly clientId: string;
  readonly expiresAtMs: number;
}

interface RedirectPkceTransaction {
  readonly verifier: string;
  readonly state: string;
  readonly nonce: string;
  readonly redirectPath: string;
  readonly linkHub: boolean;
  readonly issuer: string;
  readonly clientId: string;
  readonly tokenEndpoint: string;
}

@Injectable({ providedIn: 'root' })
export class OidcAuthService implements OnDestroy {
  private userAuth = inject(UserAuthService);
  private dir = inject(AgentDirectoryService);
  private profiles = inject(NetworkProfileService);
  private publicAuthority = inject(PairPublicAuthorityPolicy);
  private router = inject(Router);
  private popupCoordinator = inject(OidcPopupCoordinator);
  private refreshLock = inject(OidcRefreshLock);

  private _meta: OidcMeta | null = null;
  private _metaIssuer = '';
  private _sessionNonce = '';
  private refreshAuthorityBoundForWindow = false;
  private readonly deviceFlowAuthorities = new Map<string, DeviceFlowAuthorityBinding>();
  private refreshInFlight: Promise<boolean> | null = null;

  readonly loggedIn$ = this.userAuth.oidcToken$.pipe(
    map((token) => isJwtAccessTokenCurrent(token)),
    distinctUntilChanged(),
  );

  get sessionNonce(): string { return this._sessionNonce; }
  get hasNonce(): boolean { return !!this._sessionNonce; }

  get issuer(): string {
    const pinned = this.publicAuthority.oidcLoginAuthority();
    if (pinned) return pinned.issuer;
    const configured = this.profiles.current?.oidc?.issuer;
    return (typeof configured === 'string' ? configured.trim() : '') || PUBLIC_OIDC_ISSUER;
  }

  get clientId(): string {
    const pinned = this.publicAuthority.oidcLoginAuthority();
    if (pinned) return pinned.clientId;
    const configured = this.profiles.current?.oidc?.client_id;
    return (typeof configured === 'string' ? configured.trim() : '') || PUBLIC_OIDC_CLIENT_ID;
  }

  private get hubUrl(): string {
    return this.dir.list().find((agent) => agent.role === 'hub')?.url || '';
  }

  get currentUsername(): string {
    const p = this.userAuth.decodeTokenPayload(this.userAuth.oidcAccessTokenValue);
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
    this.deviceFlowAuthorities.clear();
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
   * Returns Keycloak's authorization endpoint with the registration action.
   * issuer, or an empty string if no issuer is configured.
   *
   * This URL is opened in a new tab via window.open() — there is no PKCE
   * state, no callback handling. The user fills in the keycloak-native
   * registration form and then has to click "Bei Keycloak anmelden"
   * manually to complete an OIDC login.
   *
   * Public Pair registration is pinned to the compile-time OIDC authority;
   * a mutable Hub profile can never choose the registration host or client.
   * The visibility gate remains in the IdentityBridge.
   *
   * Returns the empty string when the issuer is missing so callers (and
   * tests) can rely on a falsy result to no-op safely.
   */
  registrationUrl(): string {
    const issuer = String(this.issuer || '')
      .trim()
      .replace(/\/$/, '');
    if (!issuer) return '';
    const params = new URLSearchParams({
      client_id: PUBLIC_OIDC_CLIENT_ID,
      redirect_uri: `${location.origin}/oidc-callback`,
      response_type: 'code',
      scope: 'openid',
      kc_action: 'register',
    });
    return `${issuer}/protocol/openid-connect/auth?${params.toString()}`;
  }

  /**
   * Opens the keycloak self-registration page in a new tab. No-op when
   * no issuer is configured. Does not write to sessionStorage or localStorage
   * (no PKCE state — registration has no callback path).
   */
  registerWithKeycloak(): void {
    // enablePublicPair changes the in-memory selection synchronously before
    // refreshing it from the Hub. Registration must retain browser activation.
    void this.profiles.enablePublicPair();
    const url = this.registrationUrl();
    if (!url) return;
    window.open(url, '_blank');
  }

  async startLogin(redirectPath = '/', linkHub = false): Promise<void> {
    await this.profiles.enablePublicPair();
    const authority = this.requirePublicOidcAuthority();
    const authEndpoint = PUBLIC_OIDC_AUTHORIZATION_ENDPOINT;
    const verifier = this.randomB64Url(48);
    const state = this.randomB64Url(16);
    const nonce = this.randomB64Url(16);
    const challenge = await this.sha256B64Url(verifier);
    const redirectUri = `${location.origin}/oidc-callback`;

    sessionStorage.setItem(SS_PKCE_KEY, JSON.stringify({
      verifier,
      state,
      nonce,
      redirectPath,
      linkHub,
      issuer: authority.issuer,
      clientId: authority.clientId,
      tokenEndpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
    } satisfies RedirectPkceTransaction));
    sessionStorage.setItem(SS_NONCE_KEY, nonce);

    const params = new URLSearchParams({
      client_id: authority.clientId,
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
    const transaction = parseRedirectPkceTransaction(stored);
    if (!transaction || state !== transaction.state) return false;
    sessionStorage.removeItem(SS_PKCE_KEY);

    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: transaction.clientId,
      code,
      redirect_uri: `${location.origin}/oidc-callback`,
      code_verifier: transaction.verifier,
    });
    const r = await fetch(transaction.tokenEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!r.ok) return false;
    const tokens = await r.json();

    const idPayload = decodeOidcJwt(tokens.id_token);
    if (idPayload?.nonce !== transaction.nonce) return false;
    if (!this.isCurrentPublicAccessToken(tokens.access_token)) return false;

    this._sessionNonce = transaction.nonce;
    const commit = await this.commitAuthenticatedSession(
      tokens.access_token,
      tokens.refresh_token ?? null,
    );
    if (!commit.committed) return false;
    this.persistPublicRefreshAuthority(
      commit.refreshTokenPersisted ? tokens.refresh_token : null,
    );
    if (transaction.linkHub) {
      await this.linkCurrentHubIdentity(tokens.access_token);
    }
    await this.tryRestoreLinkedHubSession(tokens.access_token, true);
    this.router.navigateByUrl(transaction.redirectPath || '/');
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

  async startLoginPopup(_issuer?: string, _clientId?: string): Promise<void> {
    // Begin the explicit opt-in synchronously, but preserve the click's user
    // activation by opening the placeholder before awaiting Hub refresh.
    const publicPairReady = this.profiles.enablePublicPair();
    // Clear sensitive PKCE material left by the pre-coordinator implementation.
    this.removeLocalStorageItem('oidc.pkce.popup');
    const verifier = this.randomB64Url(48);
    const state = this.popupCoordinator.createState(this.randomB64Url(24));
    const nonce = this.randomB64Url(24);

    // This must happen before the first await. Otherwise browsers can discard
    // the click's user activation while discovery and PKCE are being prepared.
    const popup = window.open('about:blank', `oidc-login-${state}`, LOGIN_POPUP_FEATURES);
    if (!popup) {
      await publicPairReady;
      throw new OidcPopupLoginError(
        'popup_blocked',
        'Das Keycloak-Anmeldefenster wurde vom Browser blockiert. Bitte Pop-ups für diese Seite erlauben und erneut versuchen.',
      );
    }

    let parentSession: OidcPopupParentSession | null = null;
    let callbackReceived = false;
    try {
      await publicPairReady;
      const authority = this.requirePublicOidcAuthority();
      const normalizedIssuer = authority.issuer;
      const normalizedClientId = authority.clientId;

      let meta: OidcMeta;
      try {
        meta = assertPinnedPublicMetadata(
          await this.loadMeta(normalizedIssuer, POPUP_DISCOVERY_TIMEOUT_MS),
        );
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
    if (!this.isCurrentPublicAccessToken(accessToken)) {
      throw new OidcPopupLoginError(
        'token_exchange_failed',
        'Keycloak hat kein aktuell gültiges Zugriffstoken geliefert.',
      );
    }

    const idPayload = decodeOidcJwt(idToken);
    if (!idPayload || idPayload.nonce !== input.nonce) {
      throw new OidcPopupLoginError(
        'nonce_mismatch',
        'Die Keycloak-Antwort gehört nicht zur gestarteten Anmeldung.',
      );
    }

    // Persist only after code and nonce validation. The parent is the sole
    // writer; no access or refresh token is sent over the popup transport.
    const commit = await this.commitAuthenticatedSession(accessToken, refreshToken);
    if (!commit.committed) {
      throw new OidcPopupLoginError(
        'token_exchange_failed',
        'Eine neuere Keycloak-Anmeldung hat diesen Login ersetzt. Bitte den aktuellen Login verwenden.',
      );
    }
    this.persistPublicRefreshAuthority(commit.refreshTokenPersisted ? refreshToken : null);
    this._sessionNonce = input.nonce;
    // Pair/OIDC login is complete here. Optional Hub linking must not delay
    // the popup acknowledgement or turn a healthy Pair login into a timeout.
    void this.tryRestoreLinkedHubSession(accessToken, true);
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

  commitAuthenticatedSession(
    accessToken: string,
    refreshToken: string | null,
  ): Promise<OidcSessionCommitResult> {
    return this.refreshLock.run(
      () => this.userAuth.commitOidcSession(accessToken, refreshToken),
    );
  }

// ── T13: Silent token refresh via OIDC token endpoint ────────────────

  /**
   * Refresh using the encrypted OIDC RT from storage.
   * Returns true when this exchange commits or a newer valid session already
   * won the lock. False means the caller should retain/retry a still-current
   * access token or expire it at its JWT deadline.
   */
  refreshFromStorage(): Promise<boolean> {
    if (this.refreshInFlight) return this.refreshInFlight;
    const accessTokenBeforeLock = localStorage.getItem(
      IDENTITY_STORAGE_LAYOUT.oidc.accessToken.key,
    );
    const operation = this.refreshLock.run(
      () => this.performRefreshFromStorage(accessTokenBeforeLock),
    );
    this.refreshInFlight = operation;
    const clear = () => {
      if (this.refreshInFlight === operation) this.refreshInFlight = null;
    };
    void operation.then(clear, clear);
    return operation;
  }

  private async performRefreshFromStorage(
    accessTokenBeforeLock: string | null,
  ): Promise<boolean> {
    const storedAccessToken = localStorage.getItem(
      IDENTITY_STORAGE_LAYOUT.oidc.accessToken.key,
    );
    if (storedAccessToken !== this.userAuth.oidcAccessTokenValue) {
      this.userAuth.setOidcAccessToken(storedAccessToken);
    }
    if (
      storedAccessToken !== accessTokenBeforeLock
      && this.isCurrentPublicAccessToken(storedAccessToken)
    ) return true;
    const generation = this.userAuth.oidcSessionGenerationValue;
    const accessTokenAtStart = this.userAuth.oidcAccessTokenValue;
    const refreshToken = await this.userAuth.getOidcRefreshToken();
    if (this.userAuth.oidcSessionGenerationValue !== generation) {
      return this.isCurrentPublicAccessToken(this.userAuth.oidcAccessTokenValue);
    }
    if (!refreshToken) return false;
    if (!this.hasPinnedPublicRefreshAuthority()) return false;
    const controller = new AbortController();
    const timeout = window.setTimeout(
      () => controller.abort(),
      REFRESH_TOKEN_EXCHANGE_TIMEOUT_MS,
    );
    try {
      const body = new URLSearchParams({
        grant_type: 'refresh_token',
        client_id: PUBLIC_OIDC_CLIENT_ID,
        refresh_token: refreshToken,
      });
      const r = await fetch(PUBLIC_OIDC_TOKEN_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
        signal: controller.signal,
      });
      if (!r.ok) {
        if (r.status === 400 || r.status === 401) {
          const retired = await this.userAuth.commitOidcSession(
            accessTokenAtStart,
            null,
            generation,
            accessTokenAtStart,
          );
          if (retired.committed) this.clearRefreshAuthority();
          if (!retired.committed) {
            return this.isCurrentPublicAccessToken(this.userAuth.oidcAccessTokenValue);
          }
        }
        return false;
      }
      const tokens = await r.json() as Record<string, unknown>;
      const accessToken = typeof tokens['access_token'] === 'string'
        ? tokens['access_token']
        : '';
      const rotatedRefreshToken = typeof tokens['refresh_token'] === 'string'
        ? tokens['refresh_token']
        : refreshToken;
      if (!this.isCurrentPublicAccessToken(accessToken)) {
        return false;
      }
      const commit = await this.userAuth.commitOidcSession(
        accessToken,
        rotatedRefreshToken,
        generation,
        accessTokenAtStart,
      );
      if (!commit.committed) {
        return this.isCurrentPublicAccessToken(this.userAuth.oidcAccessTokenValue);
      }
      this.persistPublicRefreshAuthority(
        commit.refreshTokenPersisted ? rotatedRefreshToken : null,
      );
      await this.tryRestoreLinkedHubSession(accessToken, true);
      return true;
    } catch {
      return false;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async silentRefresh(): Promise<boolean> {
    return this.refreshFromStorage();
  }

  // ── T14: Keycloak end-session ────────────────────────────────────────

  logoutLocal(): void {
    this.userAuth.clearOidcSession();
    this.clearRefreshAuthority();
    this._sessionNonce = '';
  }

  async logout(): Promise<void> {
    this.logoutLocal();
    try {
      const meta = assertPinnedPublicMetadata(await this.loadMeta(PUBLIC_OIDC_ISSUER));
      const params = new URLSearchParams({
        client_id: this.clientId,
        post_logout_redirect_uri: `${location.origin}/login`,
      });
      location.href = `${meta.end_session_endpoint}?${params}`;
    } catch {
      this.router.navigate(['/login']);
    }
  }

  private async tryRestoreLinkedHubSession(
    oidcAccessToken: string,
    publicTokenFlow = false,
  ): Promise<void> {
    // Public Pair tokens are credentials for the pinned rendezvous boundary,
    // never ambient credentials for a mutable Hub profile. Hub association is
    // performed only by linkCurrentHubIdentity(), which requires an explicit
    // user action and an existing Hub token.
    const tokenIssuer = String(decodeOidcJwt(oidcAccessToken)?.iss || '').replace(/\/$/, '');
    if (
      publicTokenFlow
      || this.profiles.current.profile_id === 'public-ananta'
      || tokenIssuer === PUBLIC_OIDC_ISSUER
    ) return;
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
    await this.profiles.enablePublicPair();
    const authority = this.requirePublicOidcAuthority();
    const meta = assertPinnedPublicMetadata(await this.loadMeta(authority.issuer));
    const endpoint = meta.device_authorization_endpoint ?? PUBLIC_OIDC_DEVICE_AUTHORIZATION_ENDPOINT;
    const body = new URLSearchParams({ client_id: authority.clientId, scope: SCOPES });
    const r = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!r.ok) throw new Error(`Device flow start failed: ${r.status}`);
    const response = validateDeviceAuthResponse(await r.json());
    this.pruneDeviceFlowAuthorities();
    this.deviceFlowAuthorities.set(response.device_code, Object.freeze({
      tokenEndpoint: meta.token_endpoint,
      clientId: authority.clientId,
      expiresAtMs: Date.now() + response.expires_in * 1000,
    }));
    return response;
  }

  async pollDeviceToken(deviceCode: string, intervalSec: number): Promise<boolean> {
    this.pruneDeviceFlowAuthorities();
    const authority = this.deviceFlowAuthorities.get(deviceCode);
    if (!authority) throw new Error('oidc_device_flow_binding_missing');
    const body = new URLSearchParams({
      grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
      client_id: authority.clientId,
      device_code: deviceCode,
    });
    const r = await fetch(authority.tokenEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (r.status === 400) {
      const err = await r.json();
      if (err.error === 'authorization_pending' || err.error === 'slow_down') return false;
      this.deviceFlowAuthorities.delete(deviceCode);
      throw new Error(err.error);
    }
    if (!r.ok) {
      this.deviceFlowAuthorities.delete(deviceCode);
      return false;
    }
    const tokens = await r.json();
    this.deviceFlowAuthorities.delete(deviceCode);
    if (!this.isCurrentPublicAccessToken(tokens.access_token)) return false;
    const nonce = sessionStorage.getItem(SS_NONCE_KEY) ?? this.randomB64Url(16);
    this._sessionNonce = nonce;
    const commit = await this.commitAuthenticatedSession(
      tokens.access_token,
      tokens.refresh_token ?? null,
    );
    if (!commit.committed) return false;
    this.persistPublicRefreshAuthority(
      commit.refreshTokenPersisted ? tokens.refresh_token : null,
    );
    await this.tryRestoreLinkedHubSession(tokens.access_token, true);
    return true;
  }

  // ── Helpers ──────────────────────────────────────────────────────────

  private requirePublicOidcAuthority(): PublicOidcLoginAuthority {
    const authority = this.publicAuthority.oidcLoginAuthority();
    if (!authority) throw new Error('public_oidc_authority_not_selected');
    return authority;
  }

  private isCurrentPublicAccessToken(token: unknown): token is string {
    if (typeof token !== 'string') return false;
    const inspection = inspectJwtAccessToken(token);
    return inspection.ok && inspection.issuer === PUBLIC_OIDC_ISSUER;
  }

  private pruneDeviceFlowAuthorities(now = Date.now()): void {
    for (const [deviceCode, authority] of this.deviceFlowAuthorities) {
      if (authority.expiresAtMs <= now) this.deviceFlowAuthorities.delete(deviceCode);
    }
  }

  private persistPublicRefreshAuthority(refreshToken: unknown): void {
    if (typeof refreshToken !== 'string' || !refreshToken) {
      this.clearRefreshAuthority();
      return;
    }
    this.refreshAuthorityBoundForWindow = true;
    try {
      localStorage.setItem(REFRESH_AUTHORITY_KEY, JSON.stringify({
        version: 1,
        issuer: PUBLIC_OIDC_ISSUER,
        clientId: PUBLIC_OIDC_CLIENT_ID,
        tokenEndpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
      }));
    } catch { /* A reload will require a fresh login. */ }
  }

  private hasPinnedPublicRefreshAuthority(): boolean {
    if (this.refreshAuthorityBoundForWindow) return true;
    try {
      const value = JSON.parse(localStorage.getItem(REFRESH_AUTHORITY_KEY) || 'null') as Record<string, unknown> | null;
      const valid = value?.['version'] === 1
        && value['issuer'] === PUBLIC_OIDC_ISSUER
        && value['clientId'] === PUBLIC_OIDC_CLIENT_ID
        && value['tokenEndpoint'] === PUBLIC_OIDC_TOKEN_ENDPOINT;
      this.refreshAuthorityBoundForWindow = valid;
      return valid;
    } catch { return false; }
  }

  private clearRefreshAuthority(): void {
    this.refreshAuthorityBoundForWindow = false;
    try { localStorage.removeItem(REFRESH_AUTHORITY_KEY); } catch { /* already unavailable */ }
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

}

function validateDeviceAuthResponse(value: unknown): DeviceAuthResponse {
  if (!value || typeof value !== 'object') throw new Error('oidc_device_response_invalid');
  const response = value as Partial<DeviceAuthResponse>;
  const bounded = (candidate: unknown, maxLength: number): candidate is string => (
    typeof candidate === 'string' && candidate.length > 0 && candidate.length <= maxLength
  );
  if (
    !bounded(response.device_code, 2048)
    || !bounded(response.user_code, 256)
    || !bounded(response.verification_uri, 2048)
    || !Number.isSafeInteger(response.expires_in)
    || Number(response.expires_in) <= 0
    || Number(response.expires_in) > 86_400
    || !Number.isSafeInteger(response.interval)
    || Number(response.interval) <= 0
    || Number(response.interval) > 300
  ) throw new Error('oidc_device_response_invalid');
  return Object.freeze({ ...response }) as DeviceAuthResponse;
}

function assertPinnedPublicMetadata(meta: OidcMeta): OidcMeta {
  if (
    meta.issuer !== PUBLIC_OIDC_ISSUER
    || meta.authorization_endpoint !== PUBLIC_OIDC_AUTHORIZATION_ENDPOINT
    || meta.token_endpoint !== PUBLIC_OIDC_TOKEN_ENDPOINT
    || meta.end_session_endpoint !== PUBLIC_OIDC_END_SESSION_ENDPOINT
    || (
      meta.device_authorization_endpoint !== undefined
      && meta.device_authorization_endpoint !== PUBLIC_OIDC_DEVICE_AUTHORIZATION_ENDPOINT
    )
  ) throw new Error('public_oidc_metadata_untrusted');
  return meta;
}

function parseRedirectPkceTransaction(raw: string): RedirectPkceTransaction | null {
  try {
    const value = JSON.parse(raw) as Partial<RedirectPkceTransaction>;
    const opaque = (candidate: unknown, maxLength: number): candidate is string => (
      typeof candidate === 'string'
      && /^[A-Za-z0-9_-]+$/.test(candidate)
      && candidate.length <= maxLength
    );
    if (
      !opaque(value.verifier, 256)
      || !opaque(value.state, 256)
      || !opaque(value.nonce, 256)
      || typeof value.redirectPath !== 'string'
      || !value.redirectPath.startsWith('/')
      || value.redirectPath.startsWith('//')
      || value.redirectPath.length > 2048
      || typeof value.linkHub !== 'boolean'
      || value.issuer !== PUBLIC_OIDC_ISSUER
      || value.clientId !== PUBLIC_OIDC_CLIENT_ID
      || value.tokenEndpoint !== PUBLIC_OIDC_TOKEN_ENDPOINT
    ) return null;
    return value as RedirectPkceTransaction;
  } catch { return null; }
}
