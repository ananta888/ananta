import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Subject, debounceTime, distinctUntilChanged, filter } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';
import { WindowBridgeService } from './window-bridge.service';
import { UserAuthService } from './user-auth.service';

export interface AiSnakeConfig {
  [key: string]: string | number | boolean | null;
}

export interface AiSnakeConfigOptions {
  options: Record<string, string[]>;
  defaults: AiSnakeConfig;
  bool_keys: string[];
}

@Injectable({ providedIn: 'root' })
export class AiSnakeConfigService {
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);
  private bridge = inject(WindowBridgeService);
  private userAuth = inject(UserAuthService);

  readonly config$ = new BehaviorSubject<AiSnakeConfig>({});
  readonly options$ = new BehaviorSubject<AiSnakeConfigOptions | null>(null);
  private saveQueue$ = new Subject<AiSnakeConfig>();
  private pendingPatch: AiSnakeConfig = {};

  constructor() {
    this.saveQueue$.pipe(debounceTime(500)).subscribe(patch => this.flushPatch(patch));
    // Reload whenever the user logs in or the token refreshes
    this.userAuth.token$.pipe(
      filter(t => !!t),
      distinctUntilChanged(),
    ).subscribe(() => this.load());
  }

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  load(): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<{ ok: boolean; config: AiSnakeConfig }>(`${url}/ai-snake/config`, url).subscribe({
      next: r => { if (r?.config) this.config$.next(r.config); },
      error: (err) => {
        if (err?.status === 401) {
          try { this.userAuth.refreshToken().subscribe({ error: () => {} }); } catch {}
        }
      },
    });
    this.core.get<AiSnakeConfigOptions>(`${url}/ai-snake/config/options`, url).subscribe({
      next: r => { if (r) this.options$.next(r); },
      error: () => {},
    });
  }

  updateField(key: string, value: string | number | boolean): void {
    const current = { ...this.config$.value, [key]: value };
    this.config$.next(current);
    this.pendingPatch[key] = value;
    this.saveQueue$.next({ ...this.pendingPatch });
  }

  private flushPatch(patch: AiSnakeConfig): void {
    const url = this.hubUrl;
    if (!url || !Object.keys(patch).length) return;
    this.pendingPatch = {};
    this.core.patch<{ ok: boolean }>(`${url}/ai-snake/config`, patch, url).subscribe({
      next: () => { void this.bridge.sendAction('settings.reload'); },
      error: () => {},
    });
  }
}
