var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, map, tap } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { AgentDirectoryService } from './agent-directory.service';
let UserAuthService = class UserAuthService {
    constructor() {
        this.http = inject(HttpClient);
        this.dir = inject(AgentDirectoryService);
        this._token = new BehaviorSubject(localStorage.getItem('ananta.user.token'));
        this.token$ = this._token.asObservable();
        this._refreshToken = new BehaviorSubject(localStorage.getItem('ananta.user.refresh_token'));
        this._user = new BehaviorSubject(this.decodeTokenPayload(this.token));
        this.user$ = this._user.asObservable();
    }
    unwrapResponse(obs) {
        return obs.pipe(map((response) => {
            if (response && typeof response === 'object' && 'data' in response && 'status' in response) {
                return response.data;
            }
            return response;
        }));
    }
    get token() { return this._token.value; }
    get refreshTokenValue() { return this._refreshToken.value; }
    setTokens(token, refreshToken) {
        if (token) {
            localStorage.setItem('ananta.user.token', token);
        }
        else {
            localStorage.removeItem('ananta.user.token');
        }
        if (refreshToken) {
            localStorage.setItem('ananta.user.refresh_token', refreshToken);
        }
        else if (refreshToken === null && token === null) {
            localStorage.removeItem('ananta.user.refresh_token');
        }
        this._token.next(token);
        if (refreshToken !== undefined)
            this._refreshToken.next(refreshToken);
        this._user.next(this.decodeTokenPayload(token));
    }
    isLoggedIn() { return !!this.token; }
    logout() {
        this.setTokens(null, null);
    }
    refreshToken() {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub || !this.refreshTokenValue) {
            this.logout();
            throw new Error('No hub or refresh token');
        }
        return this.unwrapResponse(this.http.post(`${hub.url}/refresh-token`, {
            refresh_token: this.refreshTokenValue
        })).pipe(tap((res) => {
            this.setTokens(res.access_token);
        }));
    }
    changePassword(old_password, new_password) {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub)
            throw new Error('No hub found');
        return this.unwrapResponse(this.http.post(`${hub.url}/change-password`, {
            old_password,
            new_password
        }));
    }
    mfaSetup() {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub)
            throw new Error('No hub found');
        return this.unwrapResponse(this.http.post(`${hub.url}/mfa/setup`, {}));
    }
    mfaVerify(token) {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub)
            throw new Error('No hub found');
        return this.unwrapResponse(this.http.post(`${hub.url}/mfa/verify`, { token }));
    }
    mfaDisable() {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub)
            throw new Error('No hub found');
        return this.unwrapResponse(this.http.post(`${hub.url}/mfa/disable`, {}));
    }
    // Admin Methoden
    getMe() {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub)
            throw new Error('No hub found');
        return this.unwrapResponse(this.http.get(`${hub.url}/me`));
    }
    getUsers() {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub)
            throw new Error('No hub found');
        return this.unwrapResponse(this.http.get(`${hub.url}/users`));
    }
    createUser(username, password, role = 'user') {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub)
            throw new Error('No hub found');
        return this.unwrapResponse(this.http.post(`${hub.url}/users`, { username, password, role }));
    }
    deleteUser(username) {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub)
            throw new Error('No hub found');
        return this.unwrapResponse(this.http.delete(`${hub.url}/users/${username}`));
    }
    resetUserPassword(username, new_password) {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub)
            throw new Error('No hub found');
        return this.unwrapResponse(this.http.post(`${hub.url}/users/${username}/reset-password`, { new_password }));
    }
    updateUserRole(username, role) {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (!hub)
            throw new Error('No hub found');
        return this.unwrapResponse(this.http.put(`${hub.url}/users/${username}/role`, { role }));
    }
    decodeTokenPayload(token) {
        if (!token)
            return null;
        try {
            const parts = token.split('.');
            if (parts.length !== 3)
                return null;
            const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
            const padded = payload + '='.repeat((4 - (payload.length % 4)) % 4);
            return JSON.parse(atob(padded));
        }
        catch {
            return null;
        }
    }
};
UserAuthService = __decorate([
    Injectable({ providedIn: 'root' })
], UserAuthService);
export { UserAuthService };
//# sourceMappingURL=user-auth.service.js.map