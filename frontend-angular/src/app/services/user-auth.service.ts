import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class UserAuthService {
  private _token = new BehaviorSubject<string | null>(localStorage.getItem('ananta.user.token'));
  token$ = this._token.asObservable();

  private _user = new BehaviorSubject<any>(this.decodeToken(this.token));
  user$ = this._user.asObservable();

  get token() { return this._token.value; }

  setToken(token: string | null) {
    if (token) {
      localStorage.setItem('ananta.user.token', token);
    } else {
      localStorage.removeItem('ananta.user.token');
    }
    this._token.next(token);
    this._user.next(this.decodeToken(token));
  }

  isLoggedIn() { return !!this.token; }

  logout() {
    this.setToken(null);
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
