import { Injectable, inject } from '@angular/core';

import {
  PAIR_REPLAY_WINDOW_STORE,
  PairReplayWindowClaimResult,
  PairReplayWindowStorePort,
} from './e2e-replay.store';
import { SecureEnvelopeV1 } from './webrtc-secure-envelope';

export type ReplayReasonCode =
  | 'ok' | 'sender_mismatch' | 'recipient_mismatch' | 'scope_mismatch'
  | 'epoch_stale' | 'epoch_future' | 'sequence_duplicate'
  | 'sequence_too_old' | 'sequence_too_far_ahead' | 'replay_budget_exceeded'
  | 'replay_store_failed';

@Injectable({ providedIn: 'root' })
export class WebrtcReplayWindowService {
  private readonly store: PairReplayWindowStorePort = inject(PAIR_REPLAY_WINDOW_STORE);

  async accept(
    envelope: SecureEnvelopeV1,
    context: { scopeId: string; epoch: number; authenticatedSenderId: string; localPeerId: string },
    nowMs = Date.now(),
  ): Promise<ReplayReasonCode> {
    if (envelope.scope.id !== context.scopeId) return 'scope_mismatch';
    if (envelope.sender_id !== context.authenticatedSenderId) return 'sender_mismatch';
    if (envelope.recipient.kind !== 'peer' || envelope.recipient.id !== context.localPeerId) {
      return 'recipient_mismatch';
    }
    if (envelope.epoch < context.epoch) return 'epoch_stale';
    if (envelope.epoch > context.epoch) return 'epoch_future';
    let result: PairReplayWindowClaimResult;
    try {
      result = await this.store.claimSequence({
        scopeKind: envelope.scope.kind,
        scopeId: envelope.scope.id,
        epoch: envelope.epoch,
        senderId: envelope.sender_id,
        trafficClass: envelope.aad.traffic_class,
      }, envelope.sequence, nowMs);
    } catch {
      return 'replay_store_failed';
    }
    return replayReason(result);
  }

  clearScope(_scopeId: string): void {
    // An unbind is not proof that the server-issued epoch ended. Keeping the
    // durable window prevents a same-epoch rejoin from resetting replay state;
    // the store removes it only after its bounded acceptance lifetime.
  }
}

function replayReason(result: PairReplayWindowClaimResult): ReplayReasonCode {
  if (result === 'accepted') return 'ok';
  if (result === 'duplicate') return 'sequence_duplicate';
  if (result === 'too_old') return 'sequence_too_old';
  if (result === 'too_far_ahead') return 'sequence_too_far_ahead';
  return 'replay_budget_exceeded';
}
