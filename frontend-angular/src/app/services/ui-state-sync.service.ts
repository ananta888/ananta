/**
 * Continuously pushes UI state (current route + visible waypoints) to the
 * Hub's /snakes/{id}/ui-state endpoint. The AI-Snake uses this to know
 * where the user currently is in the UI, even between chat messages.
 * Uses the existing SharedViewStateService (Pair Dev infrastructure) for
 * route tracking, and queries DOM directly for visible waypoints.
 */
import { Injectable, inject, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import { debounceTime } from 'rxjs/operators';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { SharedViewStateService } from './shared-view-state.service';
import { AiSnakeChatService } from './ai-snake-chat.service';
import { AgentDirectoryService } from './agent-directory.service';

@Injectable({ providedIn: 'root' })
export class UiStateSyncService implements OnDestroy {
  private view = inject(SharedViewStateService);
  private snake = inject(AiSnakeChatService);
  private dir = inject(AgentDirectoryService);
  private http = inject(HttpClient);

  private sub: Subscription | null = null;

  start(): void {
    if (this.sub) return;
    this.sub = this.view.state$.pipe(debounceTime(400)).subscribe(state => {
      const snakeId = this.snake.snakeId$.value;
      if (!snakeId) return;
      const hubUrl = this.dir.list().find(a => a.role === 'hub')?.url || '';
      if (!hubUrl) return;
      const visible = this.getVisibleWaypoints();
      const token = this.snake.getSnakeToken();
      const headers = new HttpHeaders().set('Authorization', `Bearer ${token}`);
      this.http.put(
        `${hubUrl}/snakes/${encodeURIComponent(snakeId)}/ui-state`,
        { route: state.route, active_surface: state.activeSurface, visible_waypoints: visible },
        { headers }
      ).subscribe({ error: () => {} });
    });
  }

  stop(): void {
    this.sub?.unsubscribe();
    this.sub = null;
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
