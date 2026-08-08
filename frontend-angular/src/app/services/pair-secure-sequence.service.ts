import { Injectable, inject } from '@angular/core';

import {
  PAIR_SECURE_SEQUENCE_STORE,
  PairSecureSequenceStorePort,
} from './pair-secure-sequence.store';
import {
  MAX_SECURE_SEQUENCE,
  SecurityTrafficClass,
} from './webrtc-secure-envelope';

const IDENTIFIER_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const MAX_SECURITY_EPOCH = 2 ** 31 - 1;

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
  private readonly store: PairSecureSequenceStorePort = inject(PAIR_SECURE_SEQUENCE_STORE);

  async next(
    scopeId: string,
    epoch: number,
    senderId: string,
    trafficClass: SecurityTrafficClass,
  ): Promise<number> {
    if (
      !IDENTIFIER_RE.test(scopeId)
      || !Number.isSafeInteger(epoch)
      || epoch < 1
      || epoch > MAX_SECURITY_EPOCH
      || !IDENTIFIER_RE.test(senderId)
      || !['control', 'media', 'semantic', 'bulk'].includes(trafficClass)
    ) {
      throw new Error('secure_sequence_context_invalid');
    }
    const sequence = await this.store.next({ scopeId, epoch, senderId, trafficClass });
    if (!Number.isSafeInteger(sequence) || sequence < 1 || sequence > MAX_SECURE_SEQUENCE) {
      throw new Error('secure_sequence_state_invalid');
    }
    return sequence;
  }

  /**
   * Compatibility lifecycle hook for view teardown.
   *
   * Deliberately does not delete durable counters: unbinding a component or
   * leaving a still-live session does not retire the peer's replay window.
   * A new security epoch automatically selects a fresh sequence domain.
   */
  clearScope(_scopeId: string): void {
    // There is no process-local cache to release.
  }
}
