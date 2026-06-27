import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable, finalize, from, of, switchMap, tap } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { AgentDirectoryService } from './agent-directory.service';
import { ApiResponse, unwrapApiResponse } from './api-envelope';
import { SecureTokenStorage } from './secure-token-storage.service';
import { ProfileStateService } from './profile-state.service';

const HUB_RT_STORAGE_KEY = 'ananta.hub.refresh_token';
const OIDC_RT_STORAGE_KEY = 'ananta.oidc.refresh_token';
const LEGACY_HUB_RT_KEY = 'ananta.user.refresh_token';

@Injectable({ providedIn: 'root' })
export class UserAuthService {
  private http = inject(HttpClient);
  private dir = inject(AgentDirectoryService);
  private secureStorage = inject(SecureTokenStorage);
  private profileState = inject(ProfileStateService);
  private userRefreshInFlight = false;

  private _token = new BehaviorSubject<string | null>(localStorage.getItem('ananta.user.token'));
  token$ = this._token.asObservable();

  private _refreshToken = new BehaviorSubject<string | null>(localStorage.getItem('ananta.user.refresh_token'));
  private _oidcAccessToken = new BehaviorSubject<string | null>(localStorage.getItem('ananta.oidc.access_token'));

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
  get userPayload() { return this._user.value; }

  async setTokens(token: string | null, refreshToken?: string | null) {
    if (token) {
      localStorage.setItem('ananta.user.token', token);
    } else {
      localStorage.removeItem('ananta.user.token');
    }

    if (refreshToken) {
      const encrypted = await this.secureStorage.encrypt(refreshToken, HUB_RT_STORAGE_KEY);
      localStorage.setItem(HUB_RT_STORAGE_KEY, encrypted);
      localStorage.removeItem(LEGACY_HUB_RT_KEY);
    } else if (refreshToken === null && token === null) {
      localStorage.removeItem(HUB_RT_STORAGE_KEY);
      localStorage.removeItem(LEGACY_HUB_RT_KEY);
    }

    this._token.next(token);
    if (refreshToken !== undefined) this._refreshToken.next(refreshToken);
    this._user.next(this.decodeTokenPayload(token));
    this.refreshUserFromHub();
  }

  setOidcAccessToken(token: string | null) {
    if (token) {
      localStorage.setItem('ananta.oidc.access_token', token);
    } else {
      localStorage.removeItem('ananta.oidc.access_token');
    }

    this._oidcAccessToken.next(token);
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
    this.setOidcAccessToken(null);
  }

  refreshToken(): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      this.logout();
      throw new Error('No hub');
    }

    // Welle 5: Bei aktiver Hub↔OIDC-SSO-Bridge läuft der Refresh
    // DIREKT beim OIDC-Provider (z.B. Keycloak), nicht über Hub-RT.
    // Der Hub akzeptiert OIDC-ATs ohnehin (Welle 3: check_user_auth
    // validiert gegen JWKS), also reicht der frische OIDC-AT für alle
    // Hub-Calls — kein Hub-eigenes /refresh-token mehr nötig.
    if (this.isOidcBridgeActive()) {
      return from(this.refreshOidcDirectly()).pipe(
        switchMap((oidcAt) => {
          if (!oidcAt) {
            this.logout();
            throw new Error('OIDC refresh failed');
          }
          // Neuen OIDC-AT als aktuellen Hub-Token setzen (Hub validiert
          // ihn gegen JWKS). Hub-RT wird nicht mehr benötigt.
          this.setTokens(oidcAt, null);
          return of(oidcAt);
        }),
      );
    }

    // Legacy-Pfad: Hub-eigenes /refresh-token mit Hub-RT.
    return from(this.getHubRefreshToken()).pipe(
      switchMap((rt) => {
        if (!rt) {
          this.logout();
          throw new Error('No refresh token');
        }
        return this.unwrapResponse<any>(this.http.post(`${hub.url}/refresh-token`, {
          refresh_token: rt,
        })).pipe(
          tap((res: any) => {
            this.setTokens(res.access_token);
          }),
        );
      }),
    );
  }

  /**
   * Welle 5: Check whether the Hub↔OIDC SSO Bridge is active.
   * Source of truth is the profile's `oidc.bridge_active` flag injected
   * by the Hub's network-profile endpoint (see network_profiles.py).
   * Uses the cycle-free ProfileStateService to avoid NG0200 in tests.
   */
  private isOidcBridgeActive(): boolean {
    return this.profileState.bridgeActive;
  }

  /**
   * Refresh the OIDC access token via the standard OIDC token endpoint.
   * Returns the new access_token string, or null on failure.
   */
  private async refreshOidcDirectly(): Promise<string | null> {
    const oidcRt = await this.getOidcRefreshToken();
    if (!oidcRt) return null;
    const issuer = this.profileState.oidcIssuer;
    const clientId = this.profileState.oidcClientId;
    if (!issuer || !clientId) return null;
    try {
      const metaRes = await fetch(`${issuer.replace(/\/$/, '')}/.well-known/openid-configuration`);
      if (!metaRes.ok) return null;
      const meta = await metaRes.json() as { token_endpoint?: string };
      if (!meta.token_endpoint) return null;
      const body = new URLSearchParams({
        grant_type: 'refresh_token',
        refresh_token: oidcRt,
        client_id: clientId,
      });
      const res = await fetch(meta.token_endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      });
      if (!res.ok) return null;
      const json = await res.json() as { access_token?: string; refresh_token?: string };
      if (!json.access_token) return null;
      // RT kann rotiert werden — neuen RT verschlüsselt ablegen
      if (json.refresh_token) {
        await this.setOidcRefreshToken(json.refresh_token);
      }
      return json.access_token;
    } catch {
      return null;
    }
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
