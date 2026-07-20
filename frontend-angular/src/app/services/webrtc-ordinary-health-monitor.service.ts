import { Injectable, inject } from '@angular/core';

import { WebrtcMediaHealthService } from './webrtc-media-health.service';
import { WebrtcSessionService } from './webrtc-session.service';

export const ORDINARY_FALLBACK_HEALTH_POLICY = Object.freeze({
  sampleIntervalMs: 250,
  maximumSamples: 5,
  requiredHealthyWindows: 3,
});

/**
 * Bounded orchestration seam between raw browser stats and the data-only
 * ordinary health model. It owns no call policy and creates no background
 * loop; an SFU activation explicitly awaits this finite precondition.
 */
@Injectable({ providedIn: 'root' })
export class WebrtcOrdinaryHealthMonitorService {
  private readonly session = inject(WebrtcSessionService);
  private readonly health = inject(WebrtcMediaHealthService);

  async requireReady(
    sessionId: string,
    peerId: string,
    assertCurrent: () => void = () => undefined,
  ): Promise<void> {
    if (!sessionId || !peerId) throw new Error('ordinary_health_context_invalid');
    if (this.health.ordinaryFallbackReady(sessionId, peerId)) return;
    let previousEnd = Date.now() - ORDINARY_FALLBACK_HEALTH_POLICY.sampleIntervalMs;
    for (let sample = 0; sample < ORDINARY_FALLBACK_HEALTH_POLICY.maximumSamples; sample += 1) {
      assertCurrent();
      if (sample > 0) await delay(ORDINARY_FALLBACK_HEALTH_POLICY.sampleIntervalMs);
      assertCurrent();
      const snapshot = await this.session.ordinaryMediaStats();
      const observedAt = Math.max(previousEnd + 1, Date.now());
      this.health.ingest(
        sessionId,
        peerId,
        snapshot.connection,
        snapshot.stats,
        previousEnd,
        observedAt,
      );
      previousEnd = observedAt;
      if (this.health.ordinaryFallbackReady(sessionId, peerId)) return;
    }
    throw new Error('ordinary_fallback_not_healthy');
  }

  reset(sessionId: string, peerId: string): void {
    this.health.reset(sessionId, peerId);
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));
}
