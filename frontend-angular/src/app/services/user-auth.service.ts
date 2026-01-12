import { Injectable } from '@angular/core';
import { BehaviorSubject, Observable, tap } from 'rxjs';
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

  get token() { return this._token.value; }
  get refreshTokenValue() { return this._refreshToken.value; }

  setTokens(token: string | null, refreshToken: string | null = null) {
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

    return this.http.post(`${hub.url}/auth/refresh-token`, {
      refresh_token: this.refreshTokenValue
    }).pipe(
      tap((res: any) => {
        this.setTokens(res.token);
      })
    );
  }

  changePassword(old_password: string, new_password: string): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');

    return this.http.post(`${hub.url}/auth/change-password`, {
      old_password,
      new_password
    });
  }

  // Admin Methoden
  getUsers(): Observable<any[]> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.http.get<any[]>(`${hub.url}/auth/users`);
  }

  createUser(username: string, password: string, role: string = 'user'): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.http.post(`${hub.url}/auth/users`, { username, password, role });
  }

  deleteUser(username: string): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.http.delete(`${hub.url}/auth/users/${username}`);
  }

  resetUserPassword(username: string, new_password: string): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.http.post(`${hub.url}/auth/users/${username}/reset-password`, { new_password });
  }

  updateUserRole(username: string, role: string): Observable<any> {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) throw new Error('No hub found');
    return this.http.put(`${hub.url}/auth/users/${username}/role`, { role });
  }

  private decodeToken(token: string | null) {
    if (!token) return null;
    try {
      const parts = token.split('.');
      if (parts.length !== 3) return null;
      return JSON.parse(atob(parts[1]));
    } catch {
      return null;
    }
  }
}
