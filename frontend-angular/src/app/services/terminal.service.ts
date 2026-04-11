import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { generateJWT } from '../utils/jwt';

export type TerminalMode = 'interactive' | 'read';

export interface TerminalConnectOptions {
  baseUrl: string;
  mode: TerminalMode;
  token?: string;
  forwardParam?: string;
}

export interface TerminalEvent {
  type: string;
  data?: any;
}

@Injectable()
export class TerminalService {
  private static readonly CONNECT_TIMEOUT_MS = 900;

  private dir = inject(AgentDirectoryService);
  private userAuth = inject(UserAuthService);

  private ws?: WebSocket;
  private connectAttemptId = 0;
  private lastSuccessfulTokenByEndpoint = new Map<string, string>();
  private eventsSubject = new Subject<TerminalEvent>();
  private outputSubject = new Subject<string>();
  private stateSubject = new BehaviorSubject<'idle' | 'connecting' | 'connected' | 'disconnected' | 'error'>('idle');

  readonly events$ = this.eventsSubject.asObservable();
  readonly output$ = this.outputSubject.asObservable();
  readonly state$ = this.stateSubject.asObservable();

  async connect(options: TerminalConnectOptions): Promise<void> {
    const attemptId = ++this.connectAttemptId;
    this.disconnect();
    this.stateSubject.next('connecting');

    const endpointKey = this.endpointKey(options.baseUrl, options.mode, options.forwardParam);
    const tokens = await this.resolveTokenCandidates(options.baseUrl, options.token, endpointKey);
    const tried = new Set<string>();
    const candidates = tokens.filter((token) => {
      const key = token || '__none__';
      if (tried.has(key)) return false;
      tried.add(key);
      return true;
    });

    for (const token of candidates) {
      if (attemptId !== this.connectAttemptId) return;
      const wsUrl = this.toWsUrl(options.baseUrl, options.mode, token, options.forwardParam);
      const connected = await this.tryConnectOnce(wsUrl, attemptId);
      if (connected) {
        if (token) this.lastSuccessfulTokenByEndpoint.set(endpointKey, token);
        else this.lastSuccessfulTokenByEndpoint.delete(endpointKey);
        return;
      }
    }

    if (attemptId !== this.connectAttemptId) return;
    this.stateSubject.next('error');
    this.eventsSubject.next({ type: 'error' });
  }

  sendInput(input: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: 'input', data: input }));
  }

  sendResize(cols: number, rows: number): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({
      type: 'resize',
      cols: Math.max(1, Math.floor(cols || 0)),
      rows: Math.max(1, Math.floor(rows || 0)),
    }));
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = undefined;
    }
    if (this.stateSubject.value !== 'idle') {
      this.stateSubject.next('disconnected');
    }
  }

  private async resolveTokenCandidates(baseUrl: string, explicitToken?: string, endpointKey?: string): Promise<(string | undefined)[]> {
    const agent = this.dir.list().find(a => this.matchesBaseUrl(baseUrl, a.url));
    const userToken = this.userAuth.token || undefined;
    const agentToken = agent?.token;

    const out: (string | undefined)[] = [];
    const pushToken = async (raw?: string) => {
      if (!raw) return;
      if (raw.includes('.')) out.push(raw);
      else {
        try {
          out.push(await generateJWT({ sub: 'frontend', iat: Math.floor(Date.now() / 1000) }, raw));
        } catch {
          out.push(raw);
        }
      }
    };

    const cached = endpointKey ? this.lastSuccessfulTokenByEndpoint.get(endpointKey) : undefined;
    if (cached) {
      await pushToken(cached);
    }

    if (explicitToken) {
      await pushToken(explicitToken);
    }

    if (agent?.role === 'hub') {
      await pushToken(userToken);
      await pushToken(agentToken);
    } else {
      await pushToken(agentToken);
      await pushToken(userToken);
    }

    if (out.length === 0) out.push(undefined);
    return out;
  }

  private matchesBaseUrl(baseUrl: string, configuredUrl: string): boolean {
    try {
      const a = new URL(baseUrl);
      const b = new URL(configuredUrl);
      const aPort = a.port || (a.protocol === 'https:' ? '443' : '80');
      const bPort = b.port || (b.protocol === 'https:' ? '443' : '80');
      return a.protocol === b.protocol && a.hostname === b.hostname && aPort === bPort;
    } catch {
      return baseUrl.startsWith(configuredUrl);
    }
  }

  private async tryConnectOnce(wsUrl: string, attemptId: number): Promise<boolean> {
    return await new Promise<boolean>((resolve) => {
      const ws = new WebSocket(wsUrl);
      let settled = false;
      let ready = false;

      const finish = (ok: boolean) => {
        if (settled) return;
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
          try { ws.close(); } catch {}
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

      ws.onmessage = (event: MessageEvent<string>) => {
        const raw = event.data;
        let parsed: any;

        try {
          parsed = JSON.parse(raw);
        } catch {
          if (!ready) return;
          this.outputSubject.next(raw);
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
            try { ws.close(); } catch {}
            finish(false);
            return;
          }
          return;
        }

        if (msgType === 'output' && typeof msgData.chunk === 'string') {
          this.outputSubject.next(msgData.chunk);
          return;
        }
        this.eventsSubject.next({ type: msgType, data: msgData });
      };

      setTimeout(() => {
        if (!ready) {
          try { ws.close(); } catch {}
          finish(false);
        }
      }, TerminalService.CONNECT_TIMEOUT_MS);
    });
  }

  private endpointKey(baseUrl: string, mode: TerminalMode, forwardParam?: string): string {
    return `${baseUrl}|${mode}|${forwardParam || ''}`;
  }

  private toWsUrl(baseUrl: string, mode: TerminalMode, token?: string, forwardParam?: string): string {
    const url = new URL(baseUrl);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.pathname = '/ws/terminal';
    url.search = '';
    url.searchParams.set('mode', mode);

    if (token) url.searchParams.set('token', token);
    if (forwardParam) url.searchParams.set('forward_param', forwardParam);

    return url.toString();
  }
}
