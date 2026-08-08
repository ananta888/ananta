import { Injectable, InjectionToken, inject } from '@angular/core';

import { E2eEncryptionService } from './e2e-encryption.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { WebrtcReplayWindowService } from './webrtc-replay-window.service';
import { SecurityTrafficClass } from './webrtc-secure-envelope';

export interface OpenedPairViewPayload {
  plaintext: string;
  payloadType: string;
  senderId: string;
  sequence: number;
}

export interface PairViewCryptoPort {
  ready(scopeId: string, epoch: number): boolean;
  seal(
    plaintext: string,
    options: { scopeId: string; epoch: number; sequence: number; payloadType: string; trafficClass: SecurityTrafficClass },
  ): Promise<string>;
  open(serializedEnvelope: string, options: { scopeId: string; epoch: number }): Promise<OpenedPairViewPayload>;
  clear(scopeId: string): void;
}

@Injectable({ providedIn: 'root' })
export class PairViewCryptoService implements PairViewCryptoPort {
  private readonly encryption = inject(E2eEncryptionService);
  private readonly peerKeys = inject(WebrtcPeerKeyService);
  private readonly replay = inject(WebrtcReplayWindowService);

  ready(scopeId: string, epoch: number): boolean {
    try {
      const binding = this.peerKeys.requireBinding(true);
      return binding.scopeId === scopeId && binding.epoch === epoch;
    } catch {
      return false;
    }
  }

  async seal(
    plaintext: string,
    options: { scopeId: string; epoch: number; sequence: number; payloadType: string; trafficClass: SecurityTrafficClass },
  ): Promise<string> {
    const binding = this.peerKeys.requireBinding(true);
    if (binding.scopeId !== options.scopeId || binding.epoch !== options.epoch) {
      throw new Error('security_epoch_not_ready');
    }
    const envelope = await this.encryption.seal(
      binding,
      new TextEncoder().encode(plaintext),
      {
        sequence: options.sequence,
        payloadType: options.payloadType,
        trafficClass: options.trafficClass,
      },
    );
    return JSON.stringify(envelope);
  }

  async open(
    serializedEnvelope: string,
    options: { scopeId: string; epoch: number },
  ): Promise<OpenedPairViewPayload> {
    const binding = this.peerKeys.requireBinding(true);
    if (binding.scopeId !== options.scopeId || binding.epoch !== options.epoch) {
      throw new Error('security_epoch_not_ready');
    }
    let raw: unknown;
    try { raw = JSON.parse(serializedEnvelope); } catch { throw new Error('envelope_json_invalid'); }
    const opened = await this.encryption.open(binding, raw);
    const replay = await this.replay.accept(opened.envelope, {
      scopeId: binding.scopeId,
      epoch: binding.epoch,
      authenticatedSenderId: binding.remotePeerId,
      localPeerId: binding.localPeerId,
    });
    if (replay !== 'ok') throw new Error(replay);
    return {
      plaintext: new TextDecoder().decode(opened.plaintext),
      payloadType: opened.envelope.payload_type,
      senderId: opened.envelope.sender_id,
      sequence: opened.envelope.sequence,
    };
  }

  clear(scopeId: string): void {
    this.replay.clearScope(scopeId);
    this.peerKeys.clear();
  }
}

export const PAIR_VIEW_CRYPTO = new InjectionToken<PairViewCryptoPort>('PAIR_VIEW_CRYPTO', {
  providedIn: 'root',
  factory: () => inject(PairViewCryptoService),
});
