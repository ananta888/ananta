var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, map, retry, timeout, timer } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { generateJWT } from '../utils/jwt';
let HubApiCoreService = class HubApiCoreService {
    constructor() {
        this.http = inject(HttpClient);
        this.dir = inject(AgentDirectoryService);
        this.userAuth = inject(UserAuthService);
        this.timeoutMs = 15000;
        this.retryCount = 2;
        this.cache = new Map();
    }
    getExponentialBackoff(initialDelay = 2000, maxDelay = 60000) {
        return {
            delay: (_error, retryCount) => {
                const delay = Math.min(initialDelay * Math.pow(2, retryCount - 1), maxDelay);
                return timer(delay);
            }
        };
    }
    getHeaders(baseUrl, token) {
        let headers = new HttpHeaders();
        if (!token) {
            const hub = this.dir.list().find(a => a.role === 'hub');
            if (hub && baseUrl.startsWith(hub.url) && this.userAuth.token) {
                token = this.userAuth.token;
            }
            else {
                const agent = this.dir.list().find(a => baseUrl.startsWith(a.url));
                token = agent?.token;
            }
        }
        if (token)
            headers = headers.set('Authorization', `Bearer ${token}`);
        return { headers };
    }
    unwrapResponse(obs) {
        return obs.pipe(map((response) => {
            let current = response;
            for (let i = 0; i < 4; i += 1) {
                if (current && typeof current === 'object' && 'status' in current && 'data' in current) {
                    current = current.data;
                    continue;
                }
                break;
            }
            return current;
        }));
    }
    get(url, baseUrl, token, useRetry = true, timeoutMs) {
        const call = this.http.get(url, this.getHeaders(baseUrl, token)).pipe(timeout(timeoutMs ?? this.timeoutMs));
        return this.unwrapResponse(useRetry ? call.pipe(retry(this.retryCount)) : call);
    }
    post(url, body, baseUrl, token, useRetry = false, timeoutMs) {
        const call = this.http.post(url, body, this.getHeaders(baseUrl, token)).pipe(timeout(timeoutMs ?? this.timeoutMs));
        return this.unwrapResponse(useRetry ? call.pipe(retry(this.retryCount)) : call);
    }
    patch(url, body, baseUrl, token, timeoutMs) {
        return this.unwrapResponse(this.http.patch(url, body, this.getHeaders(baseUrl, token)).pipe(timeout(timeoutMs ?? this.timeoutMs)));
    }
    delete(url, baseUrl, token, timeoutMs) {
        return this.unwrapResponse(this.http.delete(url, this.getHeaders(baseUrl, token)).pipe(timeout(timeoutMs ?? this.timeoutMs)));
    }
    cacheGet(baseUrl, key, ttlMs) {
        const entry = this.cache.get(`${baseUrl}|${key}`);
        if (!entry)
            return null;
        if (Date.now() - entry.ts > ttlMs)
            return null;
        return entry.data;
    }
    cacheSet(baseUrl, key, data) {
        this.cache.set(`${baseUrl}|${key}`, { ts: Date.now(), data });
    }
    streamTaskLogs(baseUrl, id, token) {
        return new Observable(observer => {
            let urlStr = `${baseUrl}/tasks/${id}/stream-logs`;
            if (!token)
                token = this.dir.list().find(a => urlStr.startsWith(a.url))?.token;
            if (token)
                urlStr += (urlStr.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}`;
            const eventSource = new EventSource(urlStr);
            eventSource.onmessage = (event) => {
                try {
                    observer.next(JSON.parse(event.data));
                }
                catch { }
            };
            eventSource.onerror = (error) => {
                if (eventSource.readyState === EventSource.CLOSED)
                    observer.complete();
                else
                    observer.error(error);
            };
            return () => eventSource.close();
        }).pipe(retry(this.getExponentialBackoff()));
    }
    streamSystemEvents(baseUrl, token) {
        return new Observable(observer => {
            let urlStr = `${baseUrl}/api/system/events`;
            let eventSource = null;
            let closed = false;
            (async () => {
                let resolvedToken = token;
                const hub = this.dir.list().find(a => a.role === 'hub');
                const isHubEvents = !!hub && urlStr.startsWith(hub.url);
                if (!resolvedToken) {
                    if (isHubEvents)
                        resolvedToken = this.userAuth.token || undefined;
                    else {
                        const agent = this.dir.list().find(a => urlStr.startsWith(a.url));
                        if (agent?.token)
                            resolvedToken = await generateJWT({ sub: 'frontend', iat: Math.floor(Date.now() / 1000) }, agent.token);
                    }
                }
                if (!resolvedToken) {
                    observer.error(new Error('System events require an authenticated token'));
                    return;
                }
                urlStr += (urlStr.includes('?') ? '&' : '?') + `token=${encodeURIComponent(resolvedToken)}`;
                if (closed)
                    return;
                eventSource = new EventSource(urlStr);
                eventSource.onmessage = (event) => {
                    try {
                        observer.next(JSON.parse(event.data));
                    }
                    catch { }
                };
                eventSource.onerror = (error) => observer.error(error);
            })().catch((error) => observer.error(error));
            return () => {
                closed = true;
                if (eventSource)
                    eventSource.close();
            };
        }).pipe(retry(this.getExponentialBackoff()));
    }
};
HubApiCoreService = __decorate([
    Injectable({ providedIn: 'root' })
], HubApiCoreService);
export { HubApiCoreService };
//# sourceMappingURL=hub-api-core.service.js.map