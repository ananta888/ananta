import { Injectable, InjectionToken, inject } from '@angular/core';

import { E2eEncryptionService } from './e2e-encryption.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { WebrtcReplayWindowService } from './webrtc-replay-window.service';
import {
  SecureEnvelopeError,
  SecurityTrafficClass,
  parseSecureEnvelope,
} from './webrtc-secure-envelope';

const PAIR_VIEW_TRAFFIC: Readonly<Record<string, SecurityTrafficClass>> = Object.freeze({
  'pair.chat_message': 'semantic',
  'pair.view_delta': 'semantic',
  'pair.artifact_ref': 'semantic',
  'pair.cursor': 'control',
  'pair.control': 'control',
  'pair.snapshot_request': 'control',
});

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
    const expectedTraffic = PAIR_VIEW_TRAFFIC[options.payloadType];
    if (!expectedTraffic) throw new SecureEnvelopeError('payload_type_not_authorized');
    if (options.trafficClass !== expectedTraffic) throw new SecureEnvelopeError('traffic_class_mismatch');
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
    // Reject an unknown Pair payload or cross-traffic-class envelope before
    // AEAD opens it. E2eEncryptionService claims the nonce after decrypting,
    // so this fence must precede open() to keep invalid traffic out of both
    // nonce and sequence replay domains.
    const envelope = parseSecureEnvelope(raw);
    const expectedTraffic = PAIR_VIEW_TRAFFIC[envelope.payload_type];
    if (!expectedTraffic) throw new SecureEnvelopeError('payload_type_not_authorized');
    if (envelope.aad.content_encoding !== 'json') throw new SecureEnvelopeError('content_encoding_mismatch');
    if (envelope.aad.traffic_class !== expectedTraffic) throw new SecureEnvelopeError('traffic_class_mismatch');
    const opened = await this.encryption.open(binding, envelope);
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
