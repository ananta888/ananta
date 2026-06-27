import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable, finalize, from, switchMap, tap } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { AgentDirectoryService } from './agent-directory.service';
import { ApiResponse, unwrapApiResponse } from './api-envelope';
import { SecureTokenStorage } from './secure-token-storage.service';

const HUB_RT_STORAGE_KEY = 'ananta.hub.refresh_token';
const OIDC_RT_STORAGE_KEY = 'ananta.oidc.refresh_token';
const LEGACY_HUB_RT_KEY = 'ananta.user.refresh_token';

@Injectable({ providedIn: 'root' })
export class UserAuthService {
  private http = inject(HttpClient);
  private dir = inject(AgentDirectoryService);
  private secureStorage = inject(SecureTokenStorage);
  private userRefreshInFlight = false;

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

  refreshToken(): Observable<{ access_token: string; refresh_token?: string }> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      this.logout();
      throw new Error('No hub');
    }

    return from(this.getHubRefreshToken()).pipe(
      switchMap((rt) => {
        if (!rt) {
          this.logout();
          throw new Error('No refresh token');
        }
        return this.unwrapResponse<{ access_token: string; refresh_token?: string }>(
          this.http.post<ApiResponse<{ access_token: string; refresh_token?: string }>>(
            `${hub.url}/refresh-token`,
            { refresh_token: rt },
          )
        ).pipe(
          tap((res) => {
            void this.setTokens(res.access_token, res.refresh_token);
          }),
        );
      }),
    );
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
