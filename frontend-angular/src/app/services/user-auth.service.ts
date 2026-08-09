import { Injectable, InjectionToken, inject } from '@angular/core';
import {
  BehaviorSubject,
  Observable,
  catchError,
  defer,
  finalize,
  from,
  map,
  shareReplay,
  switchMap,
  throwError,
  timeout,
} from 'rxjs';
import { HttpClient, HttpContext } from '@angular/common/http';
import { AgentDirectoryService } from './agent-directory.service';
import { ApiResponse, unwrapApiResponse } from './api-envelope';
import { SecureTokenStorage } from './secure-token-storage.service';
import { SKIP_ACCESS_TOKEN_AUTH } from './auth-request-context';
import {
  HubRefreshSupersededError,
  HubRefreshTerminalError,
} from './hub-refresh-error';
import { IDENTITY_STORAGE_LAYOUT } from './identity/identity-storage-layout';

const HUB_RT_STORAGE_KEY = 'ananta.hub.refresh_token';
const OIDC_RT_STORAGE_KEY = 'ananta.oidc.refresh_token';
const LEGACY_HUB_RT_KEY = 'ananta.user.refresh_token';
export const DEFAULT_HUB_REFRESH_TIMEOUT_MILLISECONDS = 30_000;
export const HUB_REFRESH_TIMEOUT = new InjectionToken<number>('HUB_REFRESH_TIMEOUT', {
  providedIn: 'root',
  factory: () => DEFAULT_HUB_REFRESH_TIMEOUT_MILLISECONDS,
});

export interface OidcSessionCommitResult {
  readonly committed: boolean;
  readonly refreshTokenPersisted: boolean;
}

@Injectable({ providedIn: 'root' })
export class UserAuthService {
  private http = inject(HttpClient);
  private dir = inject(AgentDirectoryService);
  private secureStorage = inject(SecureTokenStorage);
  private hubRefreshTimeoutMilliseconds = inject(HUB_REFRESH_TIMEOUT);
  private userRefreshInFlight = false;
  private tokenRefreshInFlight$: Observable<{
    access_token: string;
    refresh_token?: string;
  }> | null = null;
  private hubSessionGeneration = 0;
  private oidcSessionGeneration = 0;

  private _token = new BehaviorSubject<string | null>(localStorage.getItem('ananta.user.token'));
  token$ = this._token.asObservable();

  private _refreshToken = new BehaviorSubject<string | null>(localStorage.getItem('ananta.user.refresh_token'));
  private _oidcAccessToken = new BehaviorSubject<string | null>(localStorage.getItem('ananta.oidc.access_token'));
  readonly oidcToken$ = this._oidcAccessToken.asObservable();

  private _user = new BehaviorSubject<any>(this.decodeTokenPayload(this.token));
  user$ = this._user.asObservable();

  constructor() {
    queueMicrotask(() => this.refreshUserFromHub());
  }

  private unwrapResponse<T>(obs: Observable<ApiResponse<T>>): Observable<T> {
    return unwrapApiResponse<T>(obs);
  }

  get token() { return this._token.value; }
  get refreshTokenValue() { return this._refreshToken.value; }
  get oidcAccessTokenValue() { return this._oidcAccessToken.value; }
  get oidcSessionGenerationValue() { return this.oidcSessionGeneration; }
  get userPayload() { return this._user.value; }

  async setTokens(token: string | null, refreshToken?: string | null) {
    const generation = ++this.hubSessionGeneration;
    await this.applyHubTokens(token, refreshToken, generation);
  }

  private async applyHubTokens(
    token: string | null,
    refreshToken: string | null | undefined,
    expectedGeneration: number,
  ): Promise<boolean> {
    let encryptedRefreshToken: string | null = null;
    let retainedRefreshToken = refreshToken;
    if (refreshToken) {
      try {
        encryptedRefreshToken = await this.secureStorage.encrypt(refreshToken, HUB_RT_STORAGE_KEY);
      } catch {
        // Plain HTTP on a non-loopback host is not a secure browser context,
        // so Web Crypto may be unavailable. Keep the short-lived access token
        // usable, but never downgrade refresh-token storage to plaintext.
        retainedRefreshToken = null;
      }
    }
    // Encryption is asynchronous. Logout or a newer login during that await
    // owns the session and fences this stale write before any storage changes.
    if (expectedGeneration !== this.hubSessionGeneration) return false;

    if (token) {
      localStorage.setItem('ananta.user.token', token);
    } else {
      localStorage.removeItem('ananta.user.token');
    }

    if (refreshToken && encryptedRefreshToken) {
      localStorage.setItem(HUB_RT_STORAGE_KEY, encryptedRefreshToken);
      localStorage.removeItem(LEGACY_HUB_RT_KEY);
    } else if (refreshToken !== undefined) {
      localStorage.removeItem(HUB_RT_STORAGE_KEY);
      localStorage.removeItem(LEGACY_HUB_RT_KEY);
    }

    this._token.next(token);
    if (refreshToken !== undefined) this._refreshToken.next(retainedRefreshToken ?? null);
    this._user.next(this.decodeTokenPayload(token));
    this.refreshUserFromHub();
    return true;
  }

  setOidcAccessToken(token: string | null) {
    this.oidcSessionGeneration += 1;
    this.writeOidcAccessToken(token);
  }

  setOidcAccessTokenIfGeneration(
    token: string | null,
    expectedGeneration: number,
    expectedAccessToken: string | null,
  ): boolean {
    if (
      this.oidcSessionGeneration !== expectedGeneration
      || localStorage.getItem('ananta.oidc.access_token') !== expectedAccessToken
    ) return false;
    this.oidcSessionGeneration += 1;
    this.writeOidcAccessToken(token);
    return true;
  }

  clearOidcSession(): void {
    this.oidcSessionGeneration += 1;
    localStorage.removeItem(OIDC_RT_STORAGE_KEY);
    localStorage.removeItem(IDENTITY_STORAGE_LAYOUT.oidc.refreshAuthority.key);
    this.writeOidcAccessToken(null);
  }

  private writeOidcAccessToken(token: string | null): void {
    if (token) {
      localStorage.setItem('ananta.oidc.access_token', token);
    } else {
      localStorage.removeItem('ananta.oidc.access_token');
    }

    this._oidcAccessToken.next(token);
  }

  /**
   * Atomically publishes an OIDC access/refresh-token pair after asynchronous
   * refresh-token encryption. When expectedGeneration is supplied, a newer
   * login/logout wins and this stale operation has no storage side effects.
   */
  async commitOidcSession(
    accessToken: string | null,
    refreshToken: string | null,
    expectedGeneration?: number,
    expectedAccessToken?: string | null,
  ): Promise<OidcSessionCommitResult> {
    const generation = expectedGeneration === undefined
      ? ++this.oidcSessionGeneration
      : expectedGeneration;
    if (!this.ownsOidcSession(generation, expectedAccessToken)) {
      return { committed: false, refreshTokenPersisted: false };
    }

    let encryptedRefreshToken: string | null = null;
    if (refreshToken) {
      try {
        encryptedRefreshToken = await this.secureStorage.encrypt(
          refreshToken,
          OIDC_RT_STORAGE_KEY,
        );
      } catch {
        // A browser without IndexedDB/WebCrypto can still use the short-lived
        // access token. Never downgrade the refresh token to plaintext.
      }
    }
    if (!this.ownsOidcSession(generation, expectedAccessToken)) {
      return { committed: false, refreshTokenPersisted: false };
    }

    // No await is allowed between the final fence and both storage writes.
    // This keeps the access/refresh pair consistent within this browser tab.
    this.oidcSessionGeneration += 1;
    if (encryptedRefreshToken) {
      localStorage.setItem(OIDC_RT_STORAGE_KEY, encryptedRefreshToken);
    } else {
      localStorage.removeItem(OIDC_RT_STORAGE_KEY);
    }
    this.writeOidcAccessToken(accessToken);
    return {
      committed: true,
      refreshTokenPersisted: encryptedRefreshToken !== null,
    };
  }

  private ownsOidcSession(
    expectedGeneration: number,
    expectedAccessToken?: string | null,
  ): boolean {
    return this.oidcSessionGeneration === expectedGeneration
      && (
        expectedAccessToken === undefined
        || localStorage.getItem('ananta.oidc.access_token') === expectedAccessToken
      );
  }

  async setOidcRefreshToken(token: string | null) {
    if (token) {
      const encrypted = await this.secureStorage.encrypt(token, OIDC_RT_STORAGE_KEY);
      localStorage.setItem(OIDC_RT_STORAGE_KEY, encrypted);
    } else {
      localStorage.removeItem(OIDC_RT_STORAGE_KEY);
    }
  }

  async getHubRefreshToken(): Promise<string | null> {
    const enc = localStorage.getItem(HUB_RT_STORAGE_KEY);
    if (!enc) return null;
    try {
      return await this.secureStorage.decrypt(enc, HUB_RT_STORAGE_KEY);
    } catch {
      return null;
    }
  }

  async getOidcRefreshToken(): Promise<string | null> {
    const enc = localStorage.getItem(OIDC_RT_STORAGE_KEY);
    if (!enc) return null;
    try {
      return await this.secureStorage.decrypt(enc, OIDC_RT_STORAGE_KEY);
    } catch {
      return null;
    }
  }

  async runStorageMigration(): Promise<void> {
    const legacyHubRt = localStorage.getItem(LEGACY_HUB_RT_KEY);
    if (legacyHubRt) {
      const encrypted = await this.secureStorage.encrypt(legacyHubRt, HUB_RT_STORAGE_KEY);
      localStorage.setItem(HUB_RT_STORAGE_KEY, encrypted);
      localStorage.removeItem(LEGACY_HUB_RT_KEY);
    }
    // OIDC-RT: if a legacy cleartext value exists in ananta.oidc.refresh_token
    // (which would be very unusual — historically the OIDC RT was only kept in memory)
    // it should be migrated. We detect "legacy" by the absence of the '.' separator
    // that our encrypted format requires.
    const existingOidc = localStorage.getItem(OIDC_RT_STORAGE_KEY);
    if (existingOidc && !existingOidc.includes('.')) {
      const encrypted = await this.secureStorage.encrypt(existingOidc, OIDC_RT_STORAGE_KEY);
      localStorage.setItem(OIDC_RT_STORAGE_KEY, encrypted);
    }
  }

  isLoggedIn() { return !!this.token; }

  logout() {
    this.setTokens(null, null);
    this.clearOidcSession();
  }

  logoutHub(): void {
    void this.setTokens(null, null);
  }

  refreshToken(): Observable<{ access_token: string; refresh_token?: string }> {
    // This single-flight is injector/tab-local. The Voice surface explicitly
    // restricts long runs to one active Ananta tab; cross-tab rotating refresh
    // tokens would otherwise require a Web-Locks/storage synchronization
    // protocol in addition to this in-process guard.
    if (this.tokenRefreshInFlight$) return this.tokenRefreshInFlight$;

    let shared!: Observable<{ access_token: string; refresh_token?: string }>;
    const operation = defer(() => {
      const generation = this.hubSessionGeneration;
      const hub = this.dir.list().find(a => a.role === 'hub');
      if (!hub) throw new HubRefreshTerminalError('No hub');
      return from(this.getHubRefreshToken()).pipe(
        switchMap((rt) => {
          if (!rt) throw new HubRefreshTerminalError('No refresh token');
          return this.unwrapResponse<{ access_token: string; refresh_token?: string }>(
            this.http.post<ApiResponse<{ access_token: string; refresh_token?: string }>>(
              `${hub.url}/refresh-token`,
              { refresh_token: rt },
              { context: new HttpContext().set(SKIP_ACCESS_TOKEN_AUTH, true) },
            ),
          ).pipe(timeout(this.hubRefreshTimeoutMilliseconds));
        }),
        switchMap((response) => from(this.applyHubTokens(
          response.access_token,
          response.refresh_token,
          generation,
        )).pipe(
          map((committed) => {
            if (!committed) throw new HubRefreshSupersededError('Hub session changed during refresh');
            return response;
          }),
        )),
        catchError((error) => (
          generation !== this.hubSessionGeneration
            ? throwError(() => new HubRefreshSupersededError('Hub session changed during refresh'))
            : throwError(() => error)
        )),
      );
    }).pipe(
      finalize(() => {
        if (this.tokenRefreshInFlight$ === shared) this.tokenRefreshInFlight$ = null;
      }),
    );
    shared = operation.pipe(shareReplay({ bufferSize: 1, refCount: false }));
    this.tokenRefreshInFlight$ = shared;
    return shared;
  }

  changePassword(old_password: string, new_password: string): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');

    return this.unwrapResponse(this.http.post(`${hub.url}/change-password`, {
      old_password,
      new_password
    }));
  }

  mfaSetup(): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.unwrapResponse(this.http.post(`${hub.url}/mfa/setup`, {}));
  }

  mfaVerify(token: string): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.unwrapResponse(this.http.post(`${hub.url}/mfa/verify`, { token }));
  }

  mfaDisable(): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.unwrapResponse(this.http.post(`${hub.url}/mfa/disable`, {}));
  }

  // Admin Methoden
  getMe(): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.unwrapResponse(this.http.get(`${hub.url}/me`));
  }

  private refreshUserFromHub(): void {
    if (this.userRefreshInFlight || !this.token) return;
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) return;

    this.userRefreshInFlight = true;
    this.http.get(`${hub.url}/me`, {
      headers: { Authorization: `Bearer ${this.token}` },
    }).pipe(
      finalize(() => {
        this.userRefreshInFlight = false;
      })
    ).subscribe({
      next: (response: any) => {
        const user = response?.data ?? response;
        if (user) {
          this._user.next(user);
        }
      },
      error: () => {},
    });
  }

  getUsers(): Observable<any[]> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.unwrapResponse<any[]>(this.http.get<any[]>(`${hub.url}/users`));
  }

  createUser(username: string, password: string, role: string = 'user'): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.unwrapResponse(this.http.post(`${hub.url}/users`, { username, password, role }));
  }

  deleteUser(username: string): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.unwrapResponse(this.http.delete(`${hub.url}/users/${username}`));
  }

  resetUserPassword(username: string, new_password: string): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.unwrapResponse(this.http.post(`${hub.url}/users/${username}/reset-password`, { new_password }));
  }

  updateUserRole(username: string, role: string): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.unwrapResponse(this.http.put(`${hub.url}/users/${username}/role`, { role }));
  }

  decodeTokenPayload(token: string | null) {
    if (!token) return null;
    try {
      const parts = token.split('.');
      if (parts.length !== 3) return null;
      const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      const padded = payload + '='.repeat((4 - (payload.length % 4)) % 4);
      return JSON.parse(atob(padded));
    } catch {
      return null;
    }
  }
}
