import { Injectable } from '@angular/core';
import { BehaviorSubject, Observable, map, tap } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { AgentDirectoryService } from './agent-directory.service';

@Injectable({ providedIn: 'root' })
export class UserAuthService {
  private _token = new BehaviorSubject<string | null>(localStorage.getItem('ananta.user.token'));
  token$ = this._token.asObservable();

  private _refreshToken = new BehaviorSubject<string | null>(localStorage.getItem('ananta.user.refresh_token'));

  private _user = new BehaviorSubject<any>(this.decodeToken(this.token));
  user$ = this._user.asObservable();

  constructor(private http: HttpClient, private dir: AgentDirectoryService) {}

  private unwrapResponse<T>(obs: Observable<any>): Observable<T> {
    return obs.pipe(
      map((response: any) => {
        if (response && typeof response === 'object' && 'data' in response && 'status' in response) {
          return response.data as T;
        }
        return response as T;
      })
    );
  }

  get token() { return this._token.value; }
  get refreshTokenValue() { return this._refreshToken.value; }

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
    this._user.next(this.decodeToken(token));
  }

  isLoggedIn() { return !!this.token; }

  logout() {
    this.setTokens(null, null);
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

  private decodeToken(token: string | null) {
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
