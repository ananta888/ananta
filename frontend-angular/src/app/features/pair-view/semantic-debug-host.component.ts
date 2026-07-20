import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  OnDestroy,
  OnInit,
  inject,
} from '@angular/core';
import { Subscription, firstValueFrom } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  SemanticDebugPage,
  SemanticMediaDebugApiService,
} from '../../services/semantic-media-debug-api.service';
import { ShareSessionService } from '../../services/share-session.service';
import {
  SemanticDebugEventView,
  SemanticDebugPanelComponent,
} from './semantic-debug-panel.component';

const MAX_VISIBLE_EVENTS = 1_000;

@Component({
  selector: 'app-semantic-debug-host',
  standalone: true,
  imports: [SemanticDebugPanelComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-semantic-debug-panel
      [events]="events"
      [nextCursor]="nextCursor"
      [loading]="loading"
      [errorCode]="errorCode"
      (nextPage)="load($event)" />
  `,
})
export class SemanticDebugHostComponent implements OnInit, OnDestroy {
  private readonly api = inject(SemanticMediaDebugApiService);
  private readonly shares = inject(ShareSessionService);
  private readonly directory = inject(AgentDirectoryService);
  private readonly changes = inject(ChangeDetectorRef);
  private readonly subscriptions = new Subscription();
  private contextKey = '';
  private hubUrl = '';
  private logicalScopes: readonly string[] = Object.freeze([]);
  private readonly scopeCursors = new Map<string, string | null>();

  events: readonly SemanticDebugEventView[] = Object.freeze([]);
  nextCursor: string | null = null;
  loading = false;
  errorCode: string | null = 'semantic_debug_session_missing';

  ngOnInit(): void {
    this.subscriptions.add(this.shares.state$.subscribe(() => this.bind()));
    this.bind();
  }

  ngOnDestroy(): void {
    this.contextKey = '';
    this.subscriptions.unsubscribe();
  }

  async load(cursor: string | null = null): Promise<void> {
    const key = this.contextKey;
    if (!key || this.loading) return;
    const continuation = cursor !== null;
    const scopes = this.logicalScopes.filter(
      (scope) => !continuation || this.scopeCursors.get(scope) !== null,
    );
    if (scopes.length === 0) return;
    this.loading = true;
    this.errorCode = null;
    this.changes.markForCheck();
    try {
      const pages = await Promise.all(scopes.map(async (scope) => ({
        scope,
        page: await firstValueFrom(this.api.page(
          this.hubUrl,
          scope,
          continuation ? this.scopeCursors.get(scope) ?? null : null,
        )),
      })));
      if (key !== this.contextKey) return;
      for (const result of pages) this.scopeCursors.set(result.scope, result.page.nextCursor);
      this.applyPages(pages.map((result) => result.page), continuation);
    } catch (error) {
      if (key === this.contextKey) this.errorCode = reasonCode(error, 'semantic_debug_load_failed');
    } finally {
      if (key === this.contextKey) {
        this.loading = false;
        this.changes.markForCheck();
      }
    }
  }

  private bind(): void {
    const session = this.shares.state$.value.session;
    const hub = this.directory.list().find((value) => value.role === 'hub')
      ?? this.directory.list().find((value) => value.name === 'hub');
    const hubUrl = String(hub?.url || '').trim().replace(/\/+$/, '');
    const key = session && hubUrl ? `${hubUrl}\u001f${session.id}` : '';
    if (key === this.contextKey) return;
    this.contextKey = key;
    this.hubUrl = hubUrl;
    this.logicalScopes = session ? Object.freeze([
      `semantic-contract:${session.id}`,
      `semantic-media-session:${session.id}`,
    ]) : Object.freeze([]);
    this.scopeCursors.clear();
    for (const scope of this.logicalScopes) this.scopeCursors.set(scope, null);
    this.events = Object.freeze([]);
    this.nextCursor = null;
    this.loading = false;
    this.errorCode = key ? null : (session ? 'semantic_debug_hub_missing' : 'semantic_debug_session_missing');
    this.changes.markForCheck();
    if (key) void this.load();
  }

  private applyPages(pages: readonly SemanticDebugPage[], append: boolean): void {
    const incoming = pages.flatMap((page) => [...page.items]);
    const combined = append ? [...this.events, ...incoming] : incoming;
    const unique = new Map(combined.map((event) => [event.event_id, event]));
    this.events = Object.freeze([...unique.values()]
      .sort((left, right) => left.created_at_ms - right.created_at_ms || left.event_id.localeCompare(right.event_id))
      .slice(-MAX_VISIBLE_EVENTS));
    this.nextCursor = [...this.scopeCursors.values()].some((value) => value !== null)
      ? 'semantic-debug-next'
      : null;
  }
}

function reasonCode(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const payload = error as { error?: { error?: { code?: unknown } }; message?: unknown };
    const nested = payload.error?.error?.code;
    if (typeof nested === 'string' && /^[a-z][a-z0-9_]{2,119}$/.test(nested)) return nested;
    if (typeof payload.message === 'string' && /^[a-z][a-z0-9_]{2,119}$/.test(payload.message)) {
      return payload.message;
    }
  }
  return fallback;
}
