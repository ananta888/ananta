import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Subject, Subscription } from 'rxjs';
import {
  SemanticDataChannelMessage,
  SemanticTrafficClass,
} from './webrtc-datachannel.service';
import { WebrtcSendOperation } from './webrtc-send-operation';
import { WebrtcTransportService } from './webrtc-transport.service';
import {
  SEMANTIC_SPEECH_CRYPTO,
  SemanticSpeechCryptoPort,
} from './semantic-speech-crypto.port';

export { SEMANTIC_SPEECH_CRYPTO, SemanticSpeechCryptoPort } from './semantic-speech-crypto.port';

export type SemanticSpeechPayloadKind =
  | 'revoke' | 'transcript_revision' | 'semantic_frame' | 'correction' | 'source_audio';

export interface SemanticSpeechPayload {
  version: 'ananta.semantic-speech.v1';
  kind: SemanticSpeechPayloadKind;
  session_id: string;
  epoch: number;
  turn_id: string;
  revision: number;
  sender_id: string;
  audience_id: string;
  consent_version: number;
  expires_at_ms: number;
  contract_digest: string;
  source_digest: string | null;
  authority?: 'provisional' | 'final' | 'corrected' | 'correction_failed' | 'missing_source';
  text?: string;
  features?: number[];
  audio_ciphertext?: string;
  reason_code?: string;
}

export interface SemanticSpeechTransportContext {
  sessionId: string;
  epoch: number;
  localPeerId: string;
  remotePeerId: string;
  consentVersion: number;
  contractDigest: string;
}

const MAX_PENDING_MESSAGES = 256;
const MAX_PENDING_BYTES = 4 * 1024 * 1024;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;

@Injectable({ providedIn: 'root' })
export class SemanticSpeechTransportService {
  private readonly transport = inject(WebrtcTransportService);
  private readonly cryptoPort = inject(SEMANTIC_SPEECH_CRYPTO);
  private subscription = new Subscription();
  private context: SemanticSpeechTransportContext | null = null;
  private generation = 0;
  private pendingMessages = 0;
  private pendingBytes = 0;
  readonly payload$ = new Subject<SemanticSpeechPayload>();
  readonly pressure$ = new BehaviorSubject<Readonly<{
    pendingMessages: number; pendingBytes: number; timers: number;
  }>>(Object.freeze({ pendingMessages: 0, pendingBytes: 0, timers: 0 }));

  start(context: SemanticSpeechTransportContext): void {
    this.validateContext(context);
    this.stop();
    this.subscription = new Subscription();
    this.context = { ...context };
    this.subscription.add(this.transport.semanticMessage$.subscribe(message => {
      void this.receive(message);
    }));
    for (const trafficClass of ['control', 'transcript', 'audio_recovery'] as const) {
      this.transport.enableSemanticTraffic(trafficClass);
    }
    this.transport.setSemanticEpoch(context.epoch);
  }

  stop(): void {
    this.generation += 1;
    this.subscription.unsubscribe();
    this.context = null;
    this.pendingMessages = 0;
    this.pendingBytes = 0;
    this.publishPressure();
  }

  async send(
    raw: SemanticSpeechPayload,
    options: { signal?: AbortSignal; deadlineMs?: number } = {},
  ): Promise<WebrtcSendOperation> {
    const context = this.requireContext();
    const generation = this.generation;
    const payload = validateSemanticSpeechPayload(raw, context, Date.now());
    if (payload.sender_id !== context.localPeerId || payload.audience_id !== context.remotePeerId) {
      throw new Error('semantic_speech_send_direction_invalid');
    }
    const encoded = new TextEncoder().encode(JSON.stringify(payload));
    if (
      this.pendingMessages + 1 > MAX_PENDING_MESSAGES
      || this.pendingBytes + encoded.byteLength > MAX_PENDING_BYTES
    ) throw new Error('semantic_speech_queue_full');
    const trafficClass = trafficClassForSpeech(payload.kind);
    this.pendingMessages += 1;
    this.pendingBytes += encoded.byteLength;
    this.publishPressure();
    try {
      const message = await this.cryptoPort.seal(encoded, trafficClass);
      if (generation !== this.generation || this.context !== context) {
        throw new Error('semantic_speech_operation_invalidated');
      }
      const operation = await this.transport.sendSemantic(message, options);
      void operation.result.finally(() => this.release(encoded.byteLength));
      return operation;
    } catch (error) {
      this.release(encoded.byteLength);
      throw error;
    }
  }

  snapshot(): Readonly<{ pendingMessages: number; pendingBytes: number; timers: number }> {
    return Object.freeze({ pendingMessages: this.pendingMessages, pendingBytes: this.pendingBytes, timers: 0 });
  }

  private async receive(message: SemanticDataChannelMessage): Promise<void> {
    const context = this.context;
    const generation = this.generation;
    if (!context || !['control', 'transcript', 'audio_recovery'].includes(message.traffic_class)) return;
    try {
      const opened = await this.cryptoPort.open(message);
      if (generation !== this.generation || this.context !== context) return;
      if (opened.byteLength > 1024 * 1024) throw new Error('semantic_speech_payload_too_large');
      const decoded = new TextDecoder('utf-8', { fatal: true }).decode(opened);
      const raw = JSON.parse(decoded) as unknown;
      const payload = validateSemanticSpeechPayload(raw, context, Date.now());
      if (payload.sender_id !== context.remotePeerId || payload.audience_id !== context.localPeerId) {
        throw new Error('semantic_speech_receive_direction_invalid');
      }
      this.payload$.next(payload);
    } catch {
      // Fail closed without logging plaintext, ciphertext or peer identifiers.
    }
  }

  private requireContext(): SemanticSpeechTransportContext {
    if (!this.context) throw new Error('semantic_speech_transport_not_started');
    return this.context;
  }

  private validateContext(context: SemanticSpeechTransportContext): void {
    if (
      !ID.test(context.sessionId) || !ID.test(context.localPeerId) || !ID.test(context.remotePeerId)
      || !DIGEST.test(context.contractDigest)
      || !Number.isSafeInteger(context.epoch) || context.epoch < 1
      || !Number.isSafeInteger(context.consentVersion) || context.consentVersion < 1
    ) throw new Error('semantic_speech_context_invalid');
  }

  private release(bytes: number): void {
    this.pendingMessages = Math.max(0, this.pendingMessages - 1);
    this.pendingBytes = Math.max(0, this.pendingBytes - bytes);
    this.publishPressure();
  }

  private publishPressure(): void { this.pressure$.next(this.snapshot()); }
}

export function trafficClassForSpeech(kind: SemanticSpeechPayloadKind): SemanticTrafficClass {
  if (kind === 'revoke') return 'control';
  if (kind === 'transcript_revision') return 'transcript';
  return 'audio_recovery';
}

export function validateSemanticSpeechPayload(
  raw: unknown,
  context: SemanticSpeechTransportContext,
  nowMs: number,
): SemanticSpeechPayload {
  const allowed = new Set([
    'version', 'kind', 'session_id', 'epoch', 'turn_id', 'revision', 'sender_id', 'audience_id',
    'consent_version', 'expires_at_ms', 'contract_digest', 'source_digest', 'authority', 'text',
    'features', 'audio_ciphertext', 'reason_code',
  ]);
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('semantic_speech_payload_invalid');
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).some(key => !allowed.has(key))) throw new Error('semantic_speech_unknown_field');
  const required = [
    'version', 'kind', 'session_id', 'epoch', 'turn_id', 'revision', 'sender_id', 'audience_id',
    'consent_version', 'expires_at_ms', 'contract_digest', 'source_digest',
  ];
  if (required.some(key => !(key in value))) throw new Error('semantic_speech_required_field_missing');
  const kind = value['kind'];
  if (!['revoke', 'transcript_revision', 'semantic_frame', 'correction', 'source_audio'].includes(String(kind))) {
    throw new Error('semantic_speech_kind_invalid');
  }
  if (
    value['version'] !== 'ananta.semantic-speech.v1'
    || value['session_id'] !== context.sessionId
    || value['epoch'] !== context.epoch
    || value['consent_version'] !== context.consentVersion
    || value['contract_digest'] !== context.contractDigest
    || !ID.test(String(value['turn_id']))
    || !ID.test(String(value['sender_id']))
    || !ID.test(String(value['audience_id']))
    || (value['sender_id'] !== context.remotePeerId && value['sender_id'] !== context.localPeerId)
    || (value['audience_id'] !== context.localPeerId && value['audience_id'] !== context.remotePeerId)
    || !Number.isSafeInteger(value['revision']) || Number(value['revision']) < 1
    || Number(value['revision']) > 2_147_483_647
    || !Number.isSafeInteger(value['expires_at_ms']) || Number(value['expires_at_ms']) <= nowMs
    || Number(value['expires_at_ms']) > nowMs + 600_000
  ) throw new Error('semantic_speech_context_mismatch');
  if (value['source_digest'] !== null && !DIGEST.test(String(value['source_digest']))) {
    throw new Error('semantic_speech_source_digest_invalid');
  }
  if (value['authority'] !== undefined && ![
    'provisional', 'final', 'corrected', 'correction_failed', 'missing_source',
  ].includes(String(value['authority']))) {
    throw new Error('semantic_speech_authority_invalid');
  }
  if (value['text'] !== undefined && (
    typeof value['text'] !== 'string'
    || new TextEncoder().encode(value['text']).byteLength > 16_384
  )) {
    throw new Error('semantic_speech_text_invalid');
  }
  if (value['features'] !== undefined) {
    if (!Array.isArray(value['features']) || value['features'].length > 160) {
      throw new Error('semantic_speech_features_invalid');
    }
    if (value['features'].some(item => typeof item !== 'number' || !Number.isFinite(item) || item < -1 || item > 1)) {
      throw new Error('semantic_speech_features_invalid');
    }
  }
  if (value['reason_code'] !== undefined && !ID.test(String(value['reason_code']))) {
    throw new Error('semantic_speech_reason_code_invalid');
  }
  if (kind === 'transcript_revision' && (
    !['provisional', 'final'].includes(String(value['authority'])) || typeof value['text'] !== 'string'
  )) throw new Error('semantic_speech_transcript_invalid');
  if (kind === 'correction' && (
    !['corrected', 'correction_failed', 'missing_source'].includes(String(value['authority']))
    || typeof value['text'] !== 'string'
  )) throw new Error('semantic_speech_correction_invalid');
  if (kind === 'semantic_frame' && !Array.isArray(value['features'])) {
    throw new Error('semantic_speech_features_invalid');
  }
  if (kind === 'source_audio') {
    if (
      value['source_digest'] === null
      || typeof value['audio_ciphertext'] !== 'string'
      || value['audio_ciphertext'].length === 0
      || value['audio_ciphertext'].length > 350_000
      || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value['audio_ciphertext'])
    ) throw new Error('semantic_speech_source_audio_invalid');
  }
  if (kind === 'revoke' && typeof value['reason_code'] !== 'string') {
    throw new Error('semantic_speech_revoke_invalid');
  }
  return value as unknown as SemanticSpeechPayload;
}
