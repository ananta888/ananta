import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable, finalize, tap } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { AgentDirectoryService } from './agent-directory.service';
import { ApiResponse, unwrapApiResponse } from './api-envelope';

@Injectable({ providedIn: 'root' })
export class UserAuthService {
  private http = inject(HttpClient);
  private dir = inject(AgentDirectoryService);
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

  setTokens(token: string | null, refreshToken?: string | null) {
    if (token) {
      localStorage.setItem('ananta.user.token', token);
    } else {
      localStorage.removeItem('ananta.user.token');
    }
    
    if (refreshToken) {
      localStorage.setItem('ananta.user.refresh_token', refreshToken);
    } else if (refreshToken === null && token === null) {
      localStorage.removeItem('ananta.user.refresh_token');
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

  isLoggedIn() { return !!this.token; }

  logout() {
    this.setTokens(null, null);
    this.setOidcAccessToken(null);
  }

  refreshToken(): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub || !this.refreshTokenValue) {
      this.logout();
      throw new Error('No hub or refresh token');
    }

    return this.unwrapResponse<any>(this.http.post(`${hub.url}/refresh-token`, {
      refresh_token: this.refreshTokenValue
    })).pipe(
      tap((res: any) => {
        this.setTokens(res.access_token);
      })
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
    this.getMe().pipe(
      finalize(() => {
        this.userRefreshInFlight = false;
      })
    ).subscribe({
      next: user => {
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
