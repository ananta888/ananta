var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { generateJWT } from '../utils/jwt';
let TerminalService = class TerminalService {
    constructor() {
        this.dir = inject(AgentDirectoryService);
        this.userAuth = inject(UserAuthService);
        this.connectAttemptId = 0;
        this.eventsSubject = new Subject();
        this.outputSubject = new Subject();
        this.stateSubject = new BehaviorSubject('idle');
        this.events$ = this.eventsSubject.asObservable();
        this.output$ = this.outputSubject.asObservable();
        this.state$ = this.stateSubject.asObservable();
    }
    async connect(options) {
        const attemptId = ++this.connectAttemptId;
        this.disconnect();
        this.stateSubject.next('connecting');
        const tokens = await this.resolveTokenCandidates(options.baseUrl, options.token);
        const tried = new Set();
        const candidates = tokens.filter((token) => {
            const key = token || '__none__';
            if (tried.has(key))
                return false;
            tried.add(key);
            return true;
        });
        for (const token of candidates) {
            if (attemptId !== this.connectAttemptId)
                return;
            const wsUrl = this.toWsUrl(options.baseUrl, options.mode, token, options.forwardParam);
            const connected = await this.tryConnectOnce(wsUrl, attemptId);
            if (connected)
                return;
        }
        if (attemptId !== this.connectAttemptId)
            return;
        this.stateSubject.next('error');
        this.eventsSubject.next({ type: 'error' });
    }
    sendInput(input) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN)
            return;
        this.ws.send(JSON.stringify({ type: 'input', data: input }));
    }
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = undefined;
        }
        if (this.stateSubject.value !== 'idle') {
            this.stateSubject.next('disconnected');
        }
    }
    async resolveTokenCandidates(baseUrl, explicitToken) {
        const agent = this.dir.list().find(a => this.matchesBaseUrl(baseUrl, a.url));
        const userToken = this.userAuth.token || undefined;
        const agentToken = agent?.token;
        const out = [];
        const pushToken = async (raw) => {
            if (!raw)
                return;
            if (raw.includes('.'))
                out.push(raw);
            else
                out.push(await generateJWT({ sub: 'frontend', iat: Math.floor(Date.now() / 1000) }, raw));
        };
        if (explicitToken) {
            await pushToken(explicitToken);
        }
        if (agent?.role === 'hub') {
            await pushToken(userToken);
            await pushToken(agentToken);
        }
        else {
            await pushToken(agentToken);
            await pushToken(userToken);
        }
        if (out.length === 0)
            out.push(undefined);
        return out;
    }
    matchesBaseUrl(baseUrl, configuredUrl) {
        try {
            const a = new URL(baseUrl);
            const b = new URL(configuredUrl);
            const aPort = a.port || (a.protocol === 'https:' ? '443' : '80');
            const bPort = b.port || (b.protocol === 'https:' ? '443' : '80');
            return a.protocol === b.protocol && a.hostname === b.hostname && aPort === bPort;
        }
        catch {
            return baseUrl.startsWith(configuredUrl);
        }
    }
    async tryConnectOnce(wsUrl, attemptId) {
        return await new Promise((resolve) => {
            const ws = new WebSocket(wsUrl);
            let settled = false;
            let ready = false;
            const finish = (ok) => {
                if (settled)
                    return;
                settled = true;
                resolve(ok);
            };
            ws.onopen = () => {
                if (attemptId !== this.connectAttemptId) {
                    ws.close();
                    finish(false);
                    return;
                }
            };
            ws.onerror = () => {
                if (!ready) {
                    try {
                        ws.close();
                    }
                    catch { }
                    finish(false);
                    return;
                }
                this.stateSubject.next('error');
                this.eventsSubject.next({ type: 'error' });
            };
            ws.onclose = () => {
                if (!ready) {
                    finish(false);
                    return;
                }
                this.stateSubject.next('disconnected');
                this.eventsSubject.next({ type: 'close' });
            };
            ws.onmessage = (event) => {
                const raw = event.data;
                let parsed;
                try {
                    parsed = JSON.parse(raw);
                }
                catch {
                    if (!ready)
                        return;
                    this.outputSubject.next(raw);
                    this.eventsSubject.next({ type: 'output', data: { chunk: raw } });
                    return;
                }
                const msgType = parsed?.type || 'message';
                const msgData = parsed?.data || {};
                if (!ready) {
                    if (msgType === 'ready') {
                        ready = true;
                        this.ws = ws;
                        this.stateSubject.next('connected');
                        this.eventsSubject.next({ type: 'open' });
                        this.eventsSubject.next({ type: msgType, data: msgData });
                        finish(true);
                        return;
                    }
                    if (msgType === 'error') {
                        try {
                            ws.close();
                        }
                        catch { }
                        finish(false);
                        return;
                    }
                    return;
                }
                this.eventsSubject.next({ type: msgType, data: msgData });
                if (msgType === 'output' && typeof msgData.chunk === 'string') {
                    this.outputSubject.next(msgData.chunk);
                }
            };
            setTimeout(() => {
                if (!ready) {
                    try {
                        ws.close();
                    }
                    catch { }
                    finish(false);
                }
            }, 2500);
        });
    }
    toWsUrl(baseUrl, mode, token, forwardParam) {
        const url = new URL(baseUrl);
        url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
        url.pathname = '/ws/terminal';
        url.search = '';
        url.searchParams.set('mode', mode);
        if (token)
            url.searchParams.set('token', token);
        if (forwardParam)
            url.searchParams.set('forward_param', forwardParam);
        return url.toString();
    }
};
TerminalService = __decorate([
    Injectable()
], TerminalService);
export { TerminalService };
//# sourceMappingURL=terminal.service.js.map