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

interface TerminalConnectAttemptResult {
  connected: boolean;
  errorMessage?: string;
}

@Injectable()
export class TerminalService {
  private static readonly CONNECT_TIMEOUT_MS = 6000;

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

    let lastErrorMessage = '';
    for (const token of candidates) {
      if (attemptId !== this.connectAttemptId) return;
      const wsUrl = this.toWsUrl(options.baseUrl, options.mode, token, options.forwardParam);
      const result = await this.tryConnectOnce(wsUrl, attemptId);
      if (result.connected) {
        if (token) this.lastSuccessfulTokenByEndpoint.set(endpointKey, token);
        else this.lastSuccessfulTokenByEndpoint.delete(endpointKey);
        return;
      }
      if (result.errorMessage) {
        lastErrorMessage = result.errorMessage;
      }
    }

    if (attemptId !== this.connectAttemptId) return;
    this.stateSubject.next('error');
    this.eventsSubject.next({ type: 'error', data: { message: lastErrorMessage || 'connection_failed' } });
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

  private async tryConnectOnce(wsUrl: string, attemptId: number): Promise<TerminalConnectAttemptResult> {
    return await new Promise<TerminalConnectAttemptResult>((resolve) => {
      const ws = new WebSocket(wsUrl);
      let settled = false;
      let ready = false;
      let preReadyErrorMessage = '';

      const finish = (ok: boolean, errorMessage?: string) => {
        if (settled) return;
        settled = true;
        resolve({ connected: ok, errorMessage: errorMessage || preReadyErrorMessage || undefined });
      };

      ws.onopen = () => {
        if (attemptId !== this.connectAttemptId) {
          ws.close();
          finish(false, 'superseded_attempt');
          return;
        }
      };

      ws.onerror = () => {
        if (!ready) {
          // Browsers don't expose error details on WebSocket error events.
          // Wait for onclose to capture code/reason for a better diagnostic.
          try { ws.close(); } catch {}
          return;
        }
        this.stateSubject.next('error');
        this.eventsSubject.next({ type: 'error', data: { message: 'websocket_error' } });
      };

      ws.onclose = (event: CloseEvent) => {
        if (!ready) {
          const code = Number(event?.code ?? 0);
          const reason = String(event?.reason || '').trim();
          const closeLabel = reason
            ? `closed_before_ready(code=${code},reason=${reason})`
            : `closed_before_ready(code=${code})`;
          preReadyErrorMessage = preReadyErrorMessage || closeLabel;
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
            const detailMessage = String(msgData?.message || msgData?.details || 'terminal_error').trim();
            preReadyErrorMessage = detailMessage || 'terminal_error';
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
          preReadyErrorMessage = preReadyErrorMessage || 'connect_timeout';
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
