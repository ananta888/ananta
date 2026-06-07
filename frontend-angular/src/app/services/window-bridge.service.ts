import { Injectable, OnDestroy } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export interface BridgeStatePayload {
  [key: string]: unknown;
}

export interface BridgeState {
  schema_version: string;
  state_version: string;
  payload: BridgeStatePayload;
}

export interface BridgeConnectionStatus {
  active: boolean;
  bridgeUrl: string;
  lastError: string | null;
}

export interface TuiAuthContext {
  hubUrl: string;
  hubToken: string;
  oidcToken: string;
}

@Injectable({ providedIn: 'root' })
export class WindowBridgeService implements OnDestroy {
  private bridgeUrl = '';
  private token = '';
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private _tuiAuth: TuiAuthContext = { hubUrl: '', hubToken: '', oidcToken: '' };

  readonly state$ = new BehaviorSubject<BridgeState | null>(null);
  readonly connection$ = new BehaviorSubject<BridgeConnectionStatus>({
    active: false,
    bridgeUrl: '',
    lastError: null,
  });

  get isActive(): boolean {
    return this.connection$.value.active;
  }

  get tuiAuthContext(): TuiAuthContext {
    return this._tuiAuth;
  }

  initFromUrlParams(): void {
    const params = new URLSearchParams(window.location.search);
    const bridge = params.get('bridge');
    const token = params.get('token');
    if (!bridge || !token) return;
    this.bridgeUrl = bridge;
    this.token = token;
    this._tuiAuth = {
      hubUrl: params.get('hub_url') ?? '',
      hubToken: params.get('hub_token') ?? '',
      oidcToken: params.get('oidc_token') ?? '',
    };
    this.connection$.next({ active: false, bridgeUrl: bridge, lastError: null });
    this.startPolling();
  }

  async sendAction(actionId: string, args: Record<string, unknown> = {}): Promise<boolean> {
    if (!this.bridgeUrl || !this.token) return false;
    try {
      const r = await fetch(`${this.bridgeUrl}/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Ananta-Window-Token': this.token },
        body: JSON.stringify({
          action_id: actionId,
          args,
          event_id: typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
        }),
      });
      return r.ok;
    } catch {
      return false;
    }
  }

  private startPolling(): void {
    if (this.pollHandle !== null) clearInterval(this.pollHandle);
    void this.fetchState();
    this.pollHandle = setInterval(() => void this.fetchState(), 700);
  }

  private async fetchState(): Promise<void> {
    if (!this.bridgeUrl || !this.token) return;
    try {
      const r = await fetch(`${this.bridgeUrl}/state`, {
        headers: { 'X-Ananta-Window-Token': this.token },
      });
      if (!r.ok) {
        this.connection$.next({ ...this.connection$.value, active: false, lastError: `HTTP ${r.status}` });
        return;
      }
      const j = await r.json();
      this.state$.next(j.state as BridgeState);
      this.connection$.next({ ...this.connection$.value, active: true, lastError: null });
    } catch (e) {
      this.connection$.next({ ...this.connection$.value, active: false, lastError: String(e) });
    }
  }

  ngOnDestroy(): void {
    if (this.pollHandle !== null) clearInterval(this.pollHandle);
  }
}
