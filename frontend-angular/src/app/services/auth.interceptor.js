var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { from, throwError, BehaviorSubject } from 'rxjs';
import { switchMap, catchError, filter, take } from 'rxjs/operators';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { generateJWT } from '../utils/jwt';
let AuthInterceptor = class AuthInterceptor {
    constructor() {
        this.dir = inject(AgentDirectoryService);
        this.userAuth = inject(UserAuthService);
        this.isRefreshing = false;
        this.refreshTokenSubject = new BehaviorSubject(null);
    }
    intercept(req, next) {
        // Falls bereits ein Authorization Header gesetzt ist, nichts tun
        if (req.headers.has('Authorization')) {
            return next.handle(req);
        }
        const agents = this.dir.list();
        // Finde den Agenten, dessen URL der Anfang der Request-URL ist
        const agent = agents.find(a => req.url.startsWith(a.url));
        let request = req;
        if (agent) {
            // Priorität 1: User-JWT für den Hub
            if (agent.role === 'hub' && this.userAuth.token) {
                request = req.clone({
                    setHeaders: {
                        Authorization: `Bearer ${this.userAuth.token}`
                    }
                });
            }
            // Priorität 2: Agent-JWT (Shared Secret) für Worker oder Hub (falls kein User-JWT)
            else if (agent.token) {
                return from(generateJWT({ sub: 'frontend', iat: Math.floor(Date.now() / 1000) }, agent.token)).pipe(switchMap(jwt => {
                    const authReq = req.clone({
                        setHeaders: {
                            Authorization: `Bearer ${jwt}`
                        }
                    });
                    return next.handle(authReq);
                }));
            }
        }
        return next.handle(request).pipe(catchError(error => {
            if (error instanceof HttpErrorResponse && error.status === 401 && !req.url.includes('/auth/refresh-token') && !req.url.includes('/login')) {
                return this.handle401Error(request, next);
            }
            return throwError(() => error);
        }));
    }
    handle401Error(request, next) {
        if (!this.isRefreshing) {
            this.isRefreshing = true;
            this.refreshTokenSubject.next(null);
            return this.userAuth.refreshToken().pipe(switchMap((res) => {
                this.isRefreshing = false;
                this.refreshTokenSubject.next(res.access_token);
                return next.handle(this.addTokenHeader(request, res.access_token));
            }), catchError((err) => {
                this.isRefreshing = false;
                this.userAuth.logout();
                return throwError(() => err);
            }));
        }
        return this.refreshTokenSubject.pipe(filter(token => token !== null), take(1), switchMap((token) => next.handle(this.addTokenHeader(request, token))));
    }
    addTokenHeader(request, token) {
        return request.clone({
            setHeaders: {
                Authorization: `Bearer ${token}`
            }
        });
    }
};
AuthInterceptor = __decorate([
    Injectable()
], AuthInterceptor);
export { AuthInterceptor };
//# sourceMappingURL=auth.interceptor.js.map