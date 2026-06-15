/**
 * Continuously pushes UI state (current route + visible waypoints) to the
 * Hub's /snakes/{id}/ui-state endpoint AND triggers proactive guide steps
 * on the visual snake when the user navigates to a new page.
 */
import { Injectable, inject, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import { debounceTime, distinctUntilChanged, map } from 'rxjs/operators';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { SharedViewStateService } from './shared-view-state.service';
import { AiSnakeChatService } from './ai-snake-chat.service';
import { AgentDirectoryService } from './agent-directory.service';
import { SnakeGuideService, GuideStep } from './snake-guide.service';
import { UiSnapshotService } from './ui-snapshot.service';
import { PredictionGateService } from './prediction-gate.service';

/** Static route → guide steps for proactive navigation hints. */
const ROUTE_GUIDE_TIPS: Record<string, GuideStep[]> = {
  '/chats': [
    { waypoint: 'chat.new-session', bubble: 'AI Chats — Session auswählen oder neue erstellen', delay_ms: 4000 },
    { waypoint: 'chat.settings-tab', bubble: 'Einstellungen: Backend & Retrieval-Profil pro Session', delay_ms: 4000 },
  ],
  '/teams': [
    { waypoint: 'teams.tab-blueprints', bubble: 'Teams & Blueprints — hier Blueprints verwalten', delay_ms: 4000 },
    { waypoint: 'teams.blueprint-catalog', bubble: 'Standard-Katalog: Blueprint auswählen oder neu erstellen', delay_ms: 4500 },
  ],
  '/dashboard': [
    { waypoint: 'nav./dashboard', bubble: 'Dashboard — Ziele und Aufgaben im Überblick', delay_ms: 4000 },
  ],
  '/workspace': [
    { waypoint: 'nav./workspace', bubble: 'Arbeitsbereich — hier arbeitest du mit Zielen', delay_ms: 4000 },
  ],
  '/board': [
    { waypoint: 'nav./board', bubble: 'Aufgaben-Board — alle Tasks auf einem Blick', delay_ms: 4000 },
  ],
  '/artifacts': [
    { waypoint: 'nav./artifacts', bubble: 'Ergebnisse — generierte Artefakte und Outputs', delay_ms: 4000 },
  ],
};

/** Prefix match for multi-segment routes like /control-center/* */
const ROUTE_PREFIX_TIPS: Array<[string, GuideStep[]]> = [
  ['/control-center', [
    { waypoint: 'cc.workers', bubble: 'Control Center — Worker, Sessions und Policy-Genehmigungen', delay_ms: 4000 },
  ]],
];

@Injectable({ providedIn: 'root' })
export class UiStateSyncService implements OnDestroy {
  private view     = inject(SharedViewStateService);
  private snake    = inject(AiSnakeChatService);
  private dir      = inject(AgentDirectoryService);
  private http     = inject(HttpClient);
  private guide    = inject(SnakeGuideService);
  private snapshot = inject(UiSnapshotService);
  private gate     = inject(PredictionGateService);

  private sub: Subscription | null = null;
  private lastRoute = '';
  private lastSnapshot = '';
  private pendingSnapshot = '';
  private dwellTimer: ReturnType<typeof setTimeout> | null = null;

  start(): void {
    if (this.sub) return;
    this.sub = this.view.state$.pipe(debounceTime(400)).subscribe(state => {
      const snakeId = this.snake.snakeId$.value;
      const hubUrl  = this.dir.list().find(a => a.role === 'hub')?.url || '';

      const uiSnapshot = this.snapshot.capture();

      // ── Push UI state + snapshot to backend ─────────────────────────────────
      if (snakeId && hubUrl) {
        const visible = this.getVisibleWaypoints();
        const token   = this.snake.getSnakeToken();
        const headers = new HttpHeaders().set('Authorization', `Bearer ${token}`);
        this.http.put(
          `${hubUrl}/snakes/${encodeURIComponent(snakeId)}/ui-state`,
          { route: state.route, active_surface: state.activeSurface, visible_waypoints: visible, ui_snapshot: uiSnapshot },
          { headers }
        ).subscribe({ error: () => {} });
      }

      // ── Visual snake tick: PUG-gated or legacy unconditional ─────────────────
      if (snakeId && hubUrl && uiSnapshot && uiSnapshot !== this.lastSnapshot) {
        const pugEnabled = this.gate.getSettings().enabled;
        if (pugEnabled) {
          this.scheduleDwellTick(snakeId, hubUrl, uiSnapshot);
        } else {
          // Legacy: send every snapshot change after a short render delay
          this.lastSnapshot = uiSnapshot;
          setTimeout(() => this.snake.sendUiContextTick(uiSnapshot), 700);
        }
      }

      // ── Proactive guide: static route tips on navigation change ─────────────
      const route = state.route.split('?')[0];
      if (route !== this.lastRoute) {
        this.lastRoute = route;
        if (this.guide.active$.value) {
          setTimeout(() => this.guide.replay(), 600);
        } else {
          const tips = this.tipsForRoute(route);
          if (tips.length) {
            setTimeout(() => this.guide.play(tips), 600);
          }
        }
      }
    });
  }

  stop(): void {
    if (this.dwellTimer) { clearTimeout(this.dwellTimer); this.dwellTimer = null; }
    this.sub?.unsubscribe();
    this.sub = null;
    this.lastRoute = '';
    this.lastSnapshot = '';
    this.pendingSnapshot = '';
  }

  private scheduleDwellTick(snakeId: string, hubUrl: string, snapshot: string): void {
    this.pendingSnapshot = snapshot;
    if (this.dwellTimer) clearTimeout(this.dwellTimer);
    const dwellMs = this.gate.getSettings().dwellMs;
    this.dwellTimer = setTimeout(() => {
      this.dwellTimer = null;
      this.tryFireTick(snakeId, hubUrl);
    }, dwellMs);
  }

  private tryFireTick(snakeId: string, hubUrl: string): void {
    const snapshot = this.pendingSnapshot;
    if (!snapshot) return;
    const result = this.gate.evaluate(snapshot);
    if (result && (result.level === 'CONFIRMED' || result.level === 'LIKELY')) {
      this.lastSnapshot = snapshot;
      this.gate.markSent(snapshot);
      this.snake.sendUiContextTick(snapshot);
    }
  }

  private tipsForRoute(route: string): GuideStep[] {
    if (ROUTE_GUIDE_TIPS[route]) return ROUTE_GUIDE_TIPS[route];
    for (const [prefix, steps] of ROUTE_PREFIX_TIPS) {
      if (route.startsWith(prefix)) return steps;
    }
    return [];
  }

  private getVisibleWaypoints(): string[] {
    const result: string[] = [];
    try {
      const els = document.querySelectorAll('[data-waypoint]');
      for (const el of Array.from(els)) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 && rect.top < window.innerHeight && rect.bottom > 0) {
          const wp = el.getAttribute('data-waypoint');
          if (wp) result.push(wp);
        }
        if (result.length >= 30) break;
      }
    } catch { /* ignore */ }
    return result;
  }

  ngOnDestroy(): void { this.stop(); }
}
