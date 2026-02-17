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
  private dir = inject(AgentDirectoryService);
  private userAuth = inject(UserAuthService);

  private ws?: WebSocket;
  private eventsSubject = new Subject<TerminalEvent>();
  private outputSubject = new Subject<string>();
  private stateSubject = new BehaviorSubject<'idle' | 'connecting' | 'connected' | 'disconnected' | 'error'>('idle');

  readonly events$ = this.eventsSubject.asObservable();
  readonly output$ = this.outputSubject.asObservable();
  readonly state$ = this.stateSubject.asObservable();

  async connect(options: TerminalConnectOptions): Promise<void> {
    this.disconnect();
    this.stateSubject.next('connecting');

    const token = await this.resolveToken(options.baseUrl, options.token);
    const wsUrl = this.toWsUrl(options.baseUrl, options.mode, token, options.forwardParam);

    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      this.stateSubject.next('connected');
      this.eventsSubject.next({ type: 'open' });
    };

    this.ws.onerror = () => {
      this.stateSubject.next('error');
      this.eventsSubject.next({ type: 'error' });
    };

    this.ws.onclose = () => {
      this.stateSubject.next('disconnected');
      this.eventsSubject.next({ type: 'close' });
    };

    this.ws.onmessage = (event: MessageEvent<string>) => {
      const raw = event.data;
      let parsed: any;

      try {
        parsed = JSON.parse(raw);
      } catch {
        this.outputSubject.next(raw);
        this.eventsSubject.next({ type: 'output', data: { chunk: raw } });
        return;
      }

      const msgType = parsed?.type || 'message';
      const msgData = parsed?.data || {};

      this.eventsSubject.next({ type: msgType, data: msgData });
      if (msgType === 'output' && typeof msgData.chunk === 'string') {
        this.outputSubject.next(msgData.chunk);
      }
    };
  }

  sendInput(input: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: 'input', data: input }));
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

  private async resolveToken(baseUrl: string, explicitToken?: string): Promise<string | undefined> {
    if (explicitToken) {
      if (explicitToken.includes('.')) return explicitToken;
      return generateJWT({ sub: 'frontend', iat: Math.floor(Date.now() / 1000) }, explicitToken);
    }

    const agent = this.dir.list().find(a => baseUrl.startsWith(a.url));
    if (!agent) return undefined;

    if (agent.role === 'hub' && this.userAuth.token) {
      return this.userAuth.token;
    }

    if (!agent.token) return undefined;
    if (agent.token.includes('.')) return agent.token;

    return generateJWT({ sub: 'frontend', iat: Math.floor(Date.now() / 1000) }, agent.token);
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
