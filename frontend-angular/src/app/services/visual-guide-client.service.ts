import { Injectable, inject } from '@angular/core';
import { AiSnakeChatService } from './ai-snake-chat.service';
import { SnakeGuideService, GuideStep } from './snake-guide.service';

export interface ClientEvent {
  event: string;
  requestId?: string;
  ts: number;
  data: unknown;
}

const MAX_BUFFER = 50;

@Injectable({ providedIn: 'root' })
export class VisualGuideClientService {
  private snake = inject(AiSnakeChatService);
  private guide = inject(SnakeGuideService);

  activeRequestId: string | null = null;

  private _clientEvents: ClientEvent[] = [];

  sendUiTick(snapshot: string): void {
    this.snake.sendUiContextTick(snapshot);
  }

  /** Sends region-explain with correlated request_id.
   *  Returns request_id for fallback control. */
  sendRegionExplain(summary: string, steps: GuideStep[]): string {
    const requestId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`;
    this.activeRequestId = requestId;
    this.guide.pendingRequestId$.next(requestId);
    const regionSteps = steps.map(s => ({
      x: s.x ?? 0,
      y: s.y ?? 0,
      bubble: s.bubble,
      waypoint: s.waypoint,
    }));
    this.snake.sendRegionExplainTick(summary, regionSteps, requestId);
    this.emitClientEvent('request_sent', { summary, stepCount: steps.length }, requestId);
    return requestId;
  }

  clearActiveRequest(requestId: string): void {
    if (this.activeRequestId === requestId) {
      this.activeRequestId = null;
      this.guide.pendingRequestId$.next(null);
      this.emitClientEvent('guide_played', {}, requestId);
    }
  }

  emitClientEvent(event: string, data: unknown, requestId?: string): void {
    this._clientEvents.push({ event, requestId, ts: Date.now(), data });
    if (this._clientEvents.length > MAX_BUFFER) {
      this._clientEvents = this._clientEvents.slice(-MAX_BUFFER);
    }
  }

  getClientEvents(requestId?: string): ClientEvent[] {
    if (!requestId) return [...this._clientEvents];
    return this._clientEvents.filter(e => e.requestId === requestId);
  }
}
