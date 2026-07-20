import { Injectable } from '@angular/core';
import { SecureEnvelopeV1 } from './webrtc-secure-envelope';

export type ReplayReasonCode =
  | 'ok' | 'sender_mismatch' | 'recipient_mismatch' | 'scope_mismatch'
  | 'epoch_stale' | 'epoch_future' | 'sequence_duplicate'
  | 'sequence_too_old' | 'sequence_too_far_ahead' | 'replay_budget_exceeded';

interface ReplayWindow {
  highest: number;
  accepted: Set<number>;
  touchedAt: number;
}

@Injectable({ providedIn: 'root' })
export class WebrtcReplayWindowService {
  private readonly windows = new Map<string, ReplayWindow>();
  private readonly windowSize = 128;
  private readonly maxWindows = 1024;
  private readonly ttlMs = 60 * 60_000;

  accept(
    envelope: SecureEnvelopeV1,
    context: { scopeId: string; epoch: number; authenticatedSenderId: string; localPeerId: string },
    nowMs = Date.now(),
  ): ReplayReasonCode {
    this.prune(nowMs);
    if (envelope.scope.id !== context.scopeId) return 'scope_mismatch';
    if (envelope.sender_id !== context.authenticatedSenderId) return 'sender_mismatch';
    if (envelope.recipient.kind !== 'peer' || envelope.recipient.id !== context.localPeerId) {
      return 'recipient_mismatch';
    }
    if (envelope.epoch < context.epoch) return 'epoch_stale';
    if (envelope.epoch > context.epoch) return 'epoch_future';
    const key = [envelope.scope.kind, envelope.scope.id, envelope.epoch,
      envelope.sender_id, envelope.aad.traffic_class].join('\u0000');
    let window = this.windows.get(key);
    if (!window) {
      if (this.windows.size >= this.maxWindows) return 'replay_budget_exceeded';
      window = { highest: 0, accepted: new Set<number>(), touchedAt: nowMs };
      this.windows.set(key, window);
    }
    if (window.accepted.has(envelope.sequence)) return 'sequence_duplicate';
    if (window.highest > 0 && envelope.sequence <= window.highest - this.windowSize) {
      return 'sequence_too_old';
    }
    if (window.highest > 0 && envelope.sequence > window.highest + 4096) {
      return 'sequence_too_far_ahead';
    }
    window.highest = Math.max(window.highest, envelope.sequence);
    window.accepted.add(envelope.sequence);
    const floor = Math.max(1, window.highest - this.windowSize + 1);
    for (const value of window.accepted) if (value < floor) window.accepted.delete(value);
    window.touchedAt = nowMs;
    return 'ok';
  }

  clearScope(scopeId: string): void {
    for (const key of this.windows.keys()) {
      if (key.split('\u0000')[1] === scopeId) this.windows.delete(key);
    }
  }

  private prune(nowMs: number): void {
    for (const [key, window] of this.windows) {
      if (window.touchedAt + this.ttlMs <= nowMs) this.windows.delete(key);
    }
  }
}
