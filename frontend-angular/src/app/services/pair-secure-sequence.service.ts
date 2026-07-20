import { Injectable } from '@angular/core';

import { SecurityTrafficClass } from './webrtc-secure-envelope';

/**
 * Allocates one monotonic sequence space per authenticated replay window.
 *
 * The Hub and browser replay guards key their windows by scope, epoch, sender
 * and traffic class.  Keeping allocation in one service prevents chat and
 * Pair-View producers from accidentally reusing a sequence under the same
 * confirmed pair key.
 */
@Injectable({ providedIn: 'root' })
export class PairSecureSequenceService {
  private readonly sequences = new Map<string, number>();

  next(scopeId: string, epoch: number, trafficClass: SecurityTrafficClass): number {
    if (!scopeId || !Number.isSafeInteger(epoch) || epoch < 1) {
      throw new Error('secure_sequence_context_invalid');
    }
    const key = `${scopeId}\u0000${epoch}\u0000${trafficClass}`;
    const current = this.sequences.get(key) ?? 0;
    if (current >= Number.MAX_SAFE_INTEGER) throw new Error('secure_sequence_exhausted');
    const next = current + 1;
    this.sequences.set(key, next);
    return next;
  }

  clearScope(scopeId: string): void {
    for (const key of this.sequences.keys()) {
      if (key.split('\u0000', 1)[0] === scopeId) this.sequences.delete(key);
    }
  }
}
