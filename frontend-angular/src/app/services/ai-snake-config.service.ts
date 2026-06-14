import { Injectable, inject, OnDestroy } from '@angular/core';
import { BehaviorSubject, Observable, Subject, catchError, debounceTime, distinctUntilChanged, filter, map, of } from 'rxjs';
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
export class AiSnakeConfigService implements OnDestroy {
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);
  private bridge = inject(WindowBridgeService);
  private userAuth = inject(UserAuthService);

  readonly config$ = new BehaviorSubject<AiSnakeConfig>({});
  readonly options$ = new BehaviorSubject<AiSnakeConfigOptions | null>(null);
  private saveQueue$ = new Subject<AiSnakeConfig>();
  private pendingPatch: AiSnakeConfig = {};
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private loadVersion = 0;

  constructor() {
    this.saveQueue$.pipe(debounceTime(500)).subscribe(patch => this.flushPatch(patch));
    // Reload whenever the user logs in or the token refreshes
    this.userAuth.token$.pipe(
      filter(t => !!t),
      distinctUntilChanged(),
    ).subscribe(() => this.load());
    // Periodic reload every 60s so stale/empty configs recover automatically
    this.pollTimer = setInterval(() => {
      if (this.hubUrl && Object.keys(this.config$.value).length === 0) {
        this.load();
      }
    }, 60_000);
  }

  ngOnDestroy(): void {
    if (this.pollTimer) clearInterval(this.pollTimer);
  }

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  load(): void {
    const version = ++this.loadVersion;
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<{ ok: boolean; config: AiSnakeConfig }>(`${url}/ai-snake/config`, url).subscribe({
      next: r => { if (r?.config && version === this.loadVersion) this.config$.next(r.config); },
      error: (err) => {
        if (err?.status === 401) {
          try { this.userAuth.refreshToken().subscribe({ error: () => {} }); } catch {}
        }
      },
    });
    this.core.get<AiSnakeConfigOptions>(`${url}/ai-snake/config/options`, url).subscribe({
      next: r => { if (r && version === this.loadVersion) this.options$.next(r); },
      error: () => {},
    });
  }

  updateField(key: string, value: string | number | boolean): void {
    const current = { ...this.config$.value, [key]: value };
    this.config$.next(current);
    this.pendingPatch[key] = value;
    this.saveQueue$.next({ ...this.pendingPatch });
  }

  listModels(): Observable<string[]> {
    const url = this.hubUrl;
    if (!url) return of([]);
    return this.core.get<{ data: Array<{ id: string }> }>(`${url}/v1/models`, url).pipe(
      map(r => (r?.data ?? []).map((m: { id: string }) => m.id)),
      catchError(() => of([])),
    );
  }

  private flushPatch(patch: AiSnakeConfig): void {
    const url = this.hubUrl;
    if (!url || !Object.keys(patch).length) return;
    this.pendingPatch = {};
    this.core.patch<{ ok: boolean }>(`${url}/ai-snake/config`, patch, url).subscribe({
      next: () => { this.load(); void this.bridge.sendAction('settings.reload'); },
      error: () => {},
    });
  }
}
