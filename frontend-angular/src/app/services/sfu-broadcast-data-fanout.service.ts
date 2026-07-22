import { Inject, Injectable, InjectionToken } from '@angular/core';

import {
  SFU_BROADCAST_DATA_LIMITS,
  type SfuBroadcastDataTrafficKind,
} from './sfu-broadcast-data-limits';
import {
  SFU_BROADCAST_GROUP_KEY_PORT,
  type SfuBroadcastCryptographicScope,
  type SfuBroadcastGroupKeyLease,
  type SfuBroadcastGroupKeyPort,
} from './sfu-broadcast-group-key.port';
import { validateSfuBroadcastGroupKeyLease } from './sfu-broadcast-e2ee-lifecycle.service';
import type { SfuDataPort, SfuOpaqueDataPacket, SfuRelease } from './sfu-room-session.ports';
import {
  WebrtcChunkReassemblyStore,
  type BoundedChunk,
  type SemanticTrafficClass,
} from './webrtc-chunk-reassembly.store';
import {
  canonicalSecurityJson,
  decodeB64,
  encodeB64,
  parseSecureEnvelope,
  secureEnvelopeAad,
  type SecureEnvelopeV1,
  type SecurityTrafficClass,
} from './webrtc-secure-envelope';

export type SfuBroadcastDataVisibility = 'shared' | 'receiver_private';

export interface SfuBroadcastDataSendRequest {
  readonly trafficKind: SfuBroadcastDataTrafficKind;
  readonly visibility: SfuBroadcastDataVisibility;
  readonly payloadType: string;
  readonly contentEncoding: 'json' | 'binary';
  readonly plaintext: Uint8Array;
  readonly destinationHandles: readonly string[];
  readonly sequence: number;
  readonly ttlMs: number;
}

export interface SfuBroadcastDataSendResult {
  readonly messageId: string;
  readonly batchCount: number;
  readonly chunkCount: number;
  readonly publishedPackets: number;
  readonly reasonCode: 'sfu_data_published';
}

export interface SfuBroadcastDataDelivery {
  readonly senderHandle: string;
  readonly payloadType: string;
  readonly trafficKind: SfuBroadcastDataTrafficKind;
  readonly sequence: number;
  readonly plaintext: Uint8Array;
}

export type SfuBroadcastDataReceiveResult = Readonly<{
  status: 'pending' | 'delivered' | 'rejected';
  reasonCode: string;
}>;

interface ActiveContext {
  readonly scope: Readonly<SfuBroadcastCryptographicScope>;
  readonly lease: SfuBroadcastGroupKeyLease;
  readonly generation: number;
}

interface WireChunk extends BoundedChunk {
  readonly audience_ref: string;
  readonly membership_epoch: number;
  readonly route_epoch: number;
  readonly key_epoch: number;
  readonly fencing_token: string;
  readonly traffic_kind: SfuBroadcastDataTrafficKind;
  readonly delivery: 'reliable' | 'lossy' | 'coalescing';
  readonly batch_index: number;
  readonly batch_count: number;
}

interface ReplayState { highest: number; touchedAtMs: number }

const DATA_TOPIC = 'ananta.sfu-data.v1';
export const SFU_BROADCAST_DATA_REASSEMBLY = new InjectionToken<WebrtcChunkReassemblyStore>(
  'SFU_BROADCAST_DATA_REASSEMBLY',
  {
    providedIn: 'root',
    factory: () => new WebrtcChunkReassemblyStore({
      maxChunksPerMessage: SFU_BROADCAST_DATA_LIMITS.publish.chunk_count_max,
      maxBytesPerMessage: SFU_BROADCAST_DATA_LIMITS.publish.envelope_bytes_max,
      maxStatesPerPeer: SFU_BROADCAST_DATA_LIMITS.receive.states_per_sender_max,
      maxStatesPerSession: SFU_BROADCAST_DATA_LIMITS.receive.states_max,
      maxBytesPerPeer: SFU_BROADCAST_DATA_LIMITS.receive.bytes_per_sender_max,
      maxBytesPerSession: SFU_BROADCAST_DATA_LIMITS.receive.bytes_max,
      maxGlobalBytes: SFU_BROADCAST_DATA_LIMITS.receive.bytes_max,
      maxStates: SFU_BROADCAST_DATA_LIMITS.receive.states_max,
      maxTtlMs: SFU_BROADCAST_DATA_LIMITS.message.ttl_ms_max,
    }),
  },
);
const WIRE_KEYS = Object.freeze([
  'version', 'chunk_id', 'message_id', 'session_id', 'epoch', 'sender_id',
  'traffic_class', 'index', 'total', 'chunk_bytes', 'total_bytes', 'expires_at_ms',
  'payload_digest', 'data', 'audience_ref', 'membership_epoch', 'route_epoch',
  'key_epoch', 'fencing_token', 'traffic_kind', 'delivery', 'batch_index', 'batch_count',
]);

@Injectable({ providedIn: 'root' })
export class SfuBroadcastDataFanoutService {
  private active: ActiveContext | null = null;
  private releaseListener: SfuRelease | null = null;
  private generation = 0;
  private sending = false;
  private readonly replay = new Map<string, ReplayState>();

  constructor(
    @Inject(SFU_BROADCAST_GROUP_KEY_PORT) private readonly keys: SfuBroadcastGroupKeyPort,
    @Inject(SFU_BROADCAST_DATA_REASSEMBLY) private readonly reassembly: WebrtcChunkReassemblyStore,
  ) {}

  async activate(scope: Readonly<SfuBroadcastCryptographicScope>, nowMs = Date.now()): Promise<void> {
    validateScope(scope);
    const generation = ++this.generation;
    const lease = await this.keys.acquire(scope, nowMs);
    try {
      validateSfuBroadcastGroupKeyLease(lease, scope, nowMs);
    } catch (error) {
      lease.release();
      throw error;
    }
    if (this.generation !== generation) {
      lease.release();
      throw new Error('sfu_data_epoch_fenced');
    }
    this.clearVolatileState();
    this.active?.lease.release();
    this.active = Object.freeze({ scope: Object.freeze({ ...scope }), lease, generation });
  }

  bind(
    port: SfuDataPort,
    handler: (delivery: SfuBroadcastDataDelivery) => void | Promise<void>,
    rejected?: (result: SfuBroadcastDataReceiveResult) => void,
  ): void {
    if (!this.active) throw new Error('sfu_data_context_inactive');
    if (!port.onOpaqueDataReceived) throw new Error('sfu_data_receive_port_unsupported');
    this.releaseListener?.();
    this.releaseListener = port.onOpaqueDataReceived(packet => {
      void this.acceptPacket(packet, handler).then(result => {
        if (result.status === 'rejected') rejected?.(result);
      });
    });
  }

  async send(
    port: SfuDataPort,
    request: Readonly<SfuBroadcastDataSendRequest>,
    nowMs = Date.now(),
  ): Promise<SfuBroadcastDataSendResult> {
    const active = this.requireActive(nowMs);
    validateSend(request);
    if (this.sending) throw new Error('sfu_data_backpressure');
    const destinations = normalizedDestinations(request.destinationHandles, active.lease);
    if (request.visibility === 'receiver_private' && destinations.length !== 1) {
      throw new Error('sfu_data_private_audience_invalid');
    }
    const batches = partition(
      destinations,
      SFU_BROADCAST_DATA_LIMITS.publish.destination_identities_per_publish_max,
    );
    if (batches.length > SFU_BROADCAST_DATA_LIMITS.publish.batch_count_max) {
      throw new Error('sfu_data_batch_count_exceeded');
    }
    const delivery = deliveryFor(request.trafficKind);
    const reliable = delivery !== 'lossy';
    const expiresAtMs = nowMs + request.ttlMs;
    const ownedPlaintext = Uint8Array.from(request.plaintext);
    let envelopeBytes: Uint8Array | null = null;
    let parts: readonly Uint8Array[] = Object.freeze([]);
    this.sending = true;
    try {
      envelopeBytes = await encryptEnvelope(active, request, ownedPlaintext, expiresAtMs);
      if (envelopeBytes.byteLength > SFU_BROADCAST_DATA_LIMITS.publish.envelope_bytes_max) {
        throw new Error('sfu_data_envelope_oversize');
      }
      const digest = await sha256Hex(envelopeBytes);
      const chunkId = await sha256Hex(new TextEncoder().encode(
        `${active.scope.roomRef}\n${active.scope.keyEpoch}\n${active.scope.localHandle}\n${digest}`,
      ));
      const payloadBytesMax = reliable
        ? SFU_BROADCAST_DATA_LIMITS.publish.reliable_chunk_payload_bytes_max
        : SFU_BROADCAST_DATA_LIMITS.publish.lossy_chunk_payload_bytes_max;
      parts = split(envelopeBytes, payloadBytesMax);
      if (parts.length > SFU_BROADCAST_DATA_LIMITS.publish.chunk_count_max) {
        throw new Error('sfu_data_chunk_count_exceeded');
      }
      let publishedPackets = 0;
      for (let batchIndex = 0; batchIndex < batches.length; batchIndex += 1) {
        for (let index = 0; index < parts.length; index += 1) {
          if (this.active?.generation !== active.generation) throw new Error('sfu_data_epoch_fenced');
          const wire = wireChunk(
            active, request, parts[index], index, parts.length, envelopeBytes.byteLength,
            digest, chunkId, delivery, batchIndex, batches.length, expiresAtMs,
          );
          const packet = new TextEncoder().encode(canonicalSecurityJson(wire));
          const packetLimit = reliable
            ? SFU_BROADCAST_DATA_LIMITS.publish.reliable_packet_bytes_max
            : SFU_BROADCAST_DATA_LIMITS.publish.lossy_packet_bytes_max;
          if (packet.byteLength > packetLimit) throw new Error('sfu_data_wire_packet_oversize');
          try {
            await port.publishOpaqueData(packet, DATA_TOPIC, batches[batchIndex], { reliable });
          } finally {
            packet.fill(0);
          }
          publishedPackets += 1;
        }
      }
      return Object.freeze({
        messageId: digest,
        batchCount: batches.length,
        chunkCount: parts.length,
        publishedPackets,
        reasonCode: 'sfu_data_published' as const,
      });
    } finally {
      for (const part of parts) part.fill(0);
      envelopeBytes?.fill(0);
      ownedPlaintext.fill(0);
      this.sending = false;
    }
  }

  async acceptPacket(
    packet: Readonly<SfuOpaqueDataPacket>,
    handler: (delivery: SfuBroadcastDataDelivery) => void | Promise<void>,
    nowMs = Date.now(),
  ): Promise<SfuBroadcastDataReceiveResult> {
    try {
      const active = this.requireActive(nowMs);
      if (packet.topic !== DATA_TOPIC || packet.payload.byteLength < 1
          || packet.payload.byteLength > SFU_BROADCAST_DATA_LIMITS.publish.reliable_packet_bytes_max) {
        return rejected('sfu_data_outer_packet_invalid');
      }
      if (!active.lease.authorizedDestinationHandles.has(packet.senderId)) {
        return rejected('sfu_data_sender_unauthorized');
      }
      const wire = parseWire(packet.payload, active, packet.senderId, nowMs);
      const assembled = await this.reassembly.accept(wire, nowMs);
      if (!this.isCurrent(active)) return rejected('sfu_data_epoch_fenced');
      if (assembled.status === 'rejected') return rejected(`sfu_data_${assembled.reason}`);
      if (assembled.status !== 'complete') {
        return Object.freeze({
          status: 'pending' as const,
          reasonCode: assembled.status === 'duplicate' ? 'sfu_data_chunk_duplicate' : 'sfu_data_chunk_pending',
        });
      }
      const envelopeBytes = assembled.value;
      try {
        const envelope = parseEnvelope(envelopeBytes, nowMs);
        const contextReason = await validateEnvelopeContext(envelope, wire, active);
        if (!this.isCurrent(active)) return rejected('sfu_data_epoch_fenced');
        if (contextReason) return rejected(contextReason);
        const replayReason = previewReplay(this.replay, envelope, nowMs);
        if (replayReason) return rejected(replayReason);
        let cleartext: Uint8Array;
        try {
          cleartext = new Uint8Array(await crypto.subtle.decrypt(
            {
              name: 'AES-GCM', iv: arrayBuffer(decodeB64(envelope.nonce_b64)),
              additionalData: arrayBuffer(secureEnvelopeAad(envelope)), tagLength: 128,
            },
            active.lease.contentKey,
            arrayBuffer(decodeB64(envelope.ciphertext_b64)),
          ));
        } catch {
          return rejected('sfu_data_authentication_failed');
        }
        const maxPlaintext = SFU_BROADCAST_DATA_LIMITS.message.plaintext_bytes_max[wire.traffic_kind];
        if (cleartext.byteLength < 1 || cleartext.byteLength > maxPlaintext) {
          cleartext.fill(0);
          return rejected('sfu_data_plaintext_size_invalid');
        }
        if (!this.isCurrent(active)) {
          cleartext.fill(0);
          return rejected('sfu_data_epoch_fenced');
        }
        commitReplay(this.replay, envelope, nowMs);
        try {
          await handler(Object.freeze({
            senderHandle: envelope.sender_id,
            payloadType: envelope.payload_type,
            trafficKind: wire.traffic_kind,
            sequence: envelope.sequence,
            plaintext: cleartext,
          }));
        } finally {
          cleartext.fill(0);
        }
        return Object.freeze({ status: 'delivered' as const, reasonCode: 'sfu_data_delivered' });
      } finally {
        envelopeBytes.fill(0);
      }
    } catch (error) {
      return rejected(reasonCode(error));
    }
  }

  revoke(reasonCode = 'sfu_data_revoked'): void {
    this.releaseListener?.();
    this.releaseListener = null;
    this.clearVolatileState();
    this.active?.lease.release();
    this.active = null;
    ++this.generation;
    void reasonCode;
  }

  destroy(): void { this.revoke('sfu_data_destroyed'); }

  snapshot(): Readonly<{ active: boolean; sending: boolean; replayWindows: number; reassemblyStates: number }> {
    return Object.freeze({
      active: this.active !== null,
      sending: this.sending,
      replayWindows: this.replay.size,
      reassemblyStates: this.reassembly.snapshot().states,
    });
  }

  private requireActive(nowMs: number): ActiveContext {
    const active = this.active;
    if (!active) throw new Error('sfu_data_context_inactive');
    validateSfuBroadcastGroupKeyLease(active.lease, active.scope, nowMs);
    return active;
  }

  private isCurrent(active: ActiveContext): boolean {
    return this.active?.generation === active.generation;
  }

  private clearVolatileState(): void {
    if (this.active) this.reassembly.clearContext(this.active.scope.roomRef);
    this.replay.clear();
    this.sending = false;
  }
}

async function encryptEnvelope(
  active: ActiveContext,
  request: Readonly<SfuBroadcastDataSendRequest>,
  plaintext: Uint8Array,
  expiresAtMs: number,
): Promise<Uint8Array> {
  const recipient = request.visibility === 'receiver_private'
    ? { kind: 'peer' as const, id: request.destinationHandles[0] }
    : { kind: 'group' as const, id: active.scope.audienceRef };
  const contractDigest = await bindingDigest(active.scope, {
    senderId: active.scope.localHandle, recipient, trafficKind: request.trafficKind,
    payloadType: request.payloadType, sequence: request.sequence, expiresAtMs,
  });
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const draft: SecureEnvelopeV1 = {
    version: 1,
    scope: { kind: 'room', id: active.scope.roomRef },
    sender_id: active.scope.localHandle,
    recipient,
    epoch: active.scope.keyEpoch,
    sequence: request.sequence,
    key_id: active.lease.keyId,
    payload_type: request.payloadType,
    expires_at_ms: expiresAtMs,
    nonce_b64: encodeB64(nonce),
    aad: {
      traffic_class: secureTrafficClass(request.trafficKind),
      content_encoding: request.contentEncoding,
      contract_digest: contractDigest,
    },
    ciphertext_b64: encodeB64(new Uint8Array(16)),
  };
  try {
    const ciphertext = await crypto.subtle.encrypt(
      {
        name: 'AES-GCM', iv: arrayBuffer(nonce),
        additionalData: arrayBuffer(secureEnvelopeAad(draft)), tagLength: 128,
      },
      active.lease.contentKey,
      arrayBuffer(plaintext),
    );
    return new TextEncoder().encode(canonicalSecurityJson({
      ...draft,
      ciphertext_b64: encodeB64(ciphertext),
    }));
  } finally {
    nonce.fill(0);
  }
}

function wireChunk(
  active: ActiveContext,
  request: Readonly<SfuBroadcastDataSendRequest>,
  part: Uint8Array,
  index: number,
  total: number,
  totalBytes: number,
  digest: string,
  chunkId: string,
  delivery: WireChunk['delivery'],
  batchIndex: number,
  batchCount: number,
  expiresAtMs: number,
): WireChunk {
  return Object.freeze({
    version: 'ananta.webrtc-bounded-chunk.v1' as const,
    chunk_id: chunkId,
    message_id: digest,
    session_id: active.scope.roomRef,
    epoch: active.scope.keyEpoch,
    sender_id: active.scope.localHandle,
    traffic_class: reassemblyTrafficClass(request.trafficKind),
    index,
    total,
    chunk_bytes: part.byteLength,
    total_bytes: totalBytes,
    expires_at_ms: expiresAtMs,
    payload_digest: digest,
    data: encodeB64(part),
    audience_ref: active.scope.audienceRef,
    membership_epoch: active.scope.membershipEpoch,
    route_epoch: active.scope.routeEpoch,
    key_epoch: active.scope.keyEpoch,
    fencing_token: active.scope.fencingToken,
    traffic_kind: request.trafficKind,
    delivery,
    batch_index: batchIndex,
    batch_count: batchCount,
  });
}

function parseWire(
  payload: Uint8Array,
  active: ActiveContext,
  authenticatedSender: string,
  nowMs: number,
): WireChunk {
  let raw: unknown;
  try { raw = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(payload)); }
  catch { throw new Error('sfu_data_wire_invalid'); }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('sfu_data_wire_invalid');
  const row = raw as Record<string, unknown>;
  if (Object.keys(row).length !== WIRE_KEYS.length
      || Object.keys(row).some(key => !WIRE_KEYS.includes(key))
      || WIRE_KEYS.some(key => !(key in row))) throw new Error('sfu_data_wire_fields_invalid');
  const wire = row as unknown as WireChunk;
  const integers = [
    wire.membership_epoch, wire.route_epoch, wire.key_epoch, wire.batch_index, wire.batch_count,
  ];
  if (wire.sender_id !== authenticatedSender || wire.session_id !== active.scope.roomRef
      || wire.audience_ref !== active.scope.audienceRef
      || wire.membership_epoch !== active.scope.membershipEpoch
      || wire.route_epoch !== active.scope.routeEpoch || wire.key_epoch !== active.scope.keyEpoch
      || wire.epoch !== active.scope.keyEpoch || wire.fencing_token !== active.scope.fencingToken
      || integers.some(value => !Number.isSafeInteger(value))
      || wire.expires_at_ms <= nowMs
      || wire.expires_at_ms - nowMs > SFU_BROADCAST_DATA_LIMITS.message.ttl_ms_max
      || !isTrafficKind(wire.traffic_kind)
      || wire.traffic_class !== reassemblyTrafficClass(wire.traffic_kind)
      || wire.delivery !== deliveryFor(wire.traffic_kind)
      || wire.batch_count < 1 || wire.batch_count > SFU_BROADCAST_DATA_LIMITS.publish.batch_count_max
      || wire.batch_index < 0 || wire.batch_index >= wire.batch_count) {
    throw new Error('sfu_data_wire_context_invalid');
  }
  const packetMax = wire.delivery === 'lossy'
    ? SFU_BROADCAST_DATA_LIMITS.publish.lossy_packet_bytes_max
    : SFU_BROADCAST_DATA_LIMITS.publish.reliable_packet_bytes_max;
  if (payload.byteLength > packetMax) throw new Error('sfu_data_wire_packet_oversize');
  return wire;
}

function parseEnvelope(value: Uint8Array, nowMs: number): SecureEnvelopeV1 {
  try {
    return parseSecureEnvelope(
      JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(value)),
      { nowMs },
    );
  } catch {
    throw new Error('sfu_data_envelope_invalid');
  }
}

async function validateEnvelopeContext(
  envelope: SecureEnvelopeV1,
  wire: WireChunk,
  active: ActiveContext,
): Promise<string> {
  if (envelope.scope.kind !== 'room' || envelope.scope.id !== active.scope.roomRef
      || envelope.sender_id !== wire.sender_id || envelope.epoch !== active.scope.keyEpoch
      || envelope.key_id !== active.lease.keyId || envelope.expires_at_ms !== wire.expires_at_ms
      || envelope.aad.traffic_class !== secureTrafficClass(wire.traffic_kind)) {
    return 'sfu_data_envelope_context_invalid';
  }
  if (envelope.recipient.kind === 'group') {
    if (envelope.recipient.id !== active.scope.audienceRef) return 'sfu_data_recipient_mismatch';
  } else if (envelope.recipient.id !== active.scope.localHandle) {
    return 'sfu_data_recipient_mismatch';
  }
  const expected = await bindingDigest(active.scope, {
    senderId: envelope.sender_id,
    recipient: envelope.recipient,
    trafficKind: wire.traffic_kind,
    payloadType: envelope.payload_type,
    sequence: envelope.sequence,
    expiresAtMs: envelope.expires_at_ms,
  });
  return expected === envelope.aad.contract_digest ? '' : 'sfu_data_binding_digest_mismatch';
}

async function bindingDigest(
  scope: Readonly<SfuBroadcastCryptographicScope>,
  value: Readonly<{
    senderId: string;
    recipient: SecureEnvelopeV1['recipient'];
    trafficKind: SfuBroadcastDataTrafficKind;
    payloadType: string;
    sequence: number;
    expiresAtMs: number;
  }>,
): Promise<string> {
  return sha256Hex(new TextEncoder().encode(canonicalSecurityJson({
    domain: 'ananta.sfu-broadcast-data-binding.v1',
    tenant_ref: scope.tenantRef,
    room_ref: scope.roomRef,
    publication_ref: scope.publicationRef,
    audience_ref: scope.audienceRef,
    membership_epoch: scope.membershipEpoch,
    route_epoch: scope.routeEpoch,
    key_epoch: scope.keyEpoch,
    fencing_token: scope.fencingToken,
    sender_id: value.senderId,
    recipient: value.recipient,
    traffic_kind: value.trafficKind,
    payload_type: value.payloadType,
    sequence: value.sequence,
    expires_at_ms: value.expiresAtMs,
  })));
}

function previewReplay(
  windows: Map<string, ReplayState>,
  envelope: SecureEnvelopeV1,
  nowMs: number,
): string {
  pruneReplay(windows, nowMs);
  const key = replayKey(envelope);
  const current = windows.get(key);
  if (!current) {
    if (windows.size >= SFU_BROADCAST_DATA_LIMITS.receive.replay_windows_max) {
      return 'sfu_data_replay_budget_exceeded';
    }
    return '';
  }
  if (envelope.sequence === current.highest) return 'sfu_data_sequence_duplicate';
  if (envelope.sequence < current.highest) return 'sfu_data_sequence_reordered';
  if (envelope.sequence > current.highest + 1) return 'sfu_data_sequence_gap';
  return '';
}

function commitReplay(windows: Map<string, ReplayState>, envelope: SecureEnvelopeV1, nowMs: number): void {
  windows.set(replayKey(envelope), { highest: envelope.sequence, touchedAtMs: nowMs });
}

function pruneReplay(windows: Map<string, ReplayState>, nowMs: number): void {
  for (const [key, state] of windows) {
    if (state.touchedAtMs + SFU_BROADCAST_DATA_LIMITS.message.ttl_ms_max <= nowMs) windows.delete(key);
  }
}

function replayKey(value: SecureEnvelopeV1): string {
  return [value.scope.id, value.epoch, value.sender_id, value.recipient.kind, value.recipient.id,
    value.aad.traffic_class].join('\0');
}

function validateSend(value: Readonly<SfuBroadcastDataSendRequest>): void {
  if (!isTrafficKind(value.trafficKind) || !['shared', 'receiver_private'].includes(value.visibility)
      || !/^[a-z][a-z0-9_.-]{0,63}$/.test(value.payloadType)
      || !['json', 'binary'].includes(value.contentEncoding)
      || !(value.plaintext instanceof Uint8Array)
      || value.plaintext.byteLength < 1
      || value.plaintext.byteLength > SFU_BROADCAST_DATA_LIMITS.message.plaintext_bytes_max[value.trafficKind]
      || !Number.isSafeInteger(value.sequence) || value.sequence < 1
      || !Number.isSafeInteger(value.ttlMs) || value.ttlMs < 1
      || value.ttlMs > SFU_BROADCAST_DATA_LIMITS.message.ttl_ms_max) {
    throw new Error('sfu_data_request_invalid');
  }
}

function validateScope(value: Readonly<SfuBroadcastCryptographicScope>): void {
  const ids = [value.tenantRef, value.roomRef, value.publicationRef, value.audienceRef,
    value.localHandle, value.fencingToken];
  if (ids.some(item => !identifier(item))
      || [value.membershipEpoch, value.routeEpoch, value.keyEpoch]
        .some(item => !Number.isSafeInteger(item) || item < 1)) {
    throw new Error('sfu_data_scope_invalid');
  }
}

function normalizedDestinations(
  values: readonly string[],
  lease: SfuBroadcastGroupKeyLease,
): readonly string[] {
  const destinations = [...new Set(values)].sort();
  if (!destinations.length || destinations.length !== values.length
      || destinations.some(value => !identifier(value)
        || !lease.authorizedDestinationHandles.has(value))) {
    throw new Error('sfu_data_audience_unauthorized');
  }
  return destinations;
}

function deliveryFor(value: SfuBroadcastDataTrafficKind): WireChunk['delivery'] {
  if (value === 'control_hint') return 'lossy';
  if (value === 'interrupt' || value === 'transcript_revision') return 'coalescing';
  return 'reliable';
}

function secureTrafficClass(value: SfuBroadcastDataTrafficKind): SecurityTrafficClass {
  if (value === 'private_recovery') return 'media';
  if (value === 'transcript_revision' || value === 'shared_reference') return 'semantic';
  return 'control';
}

function reassemblyTrafficClass(value: SfuBroadcastDataTrafficKind): SemanticTrafficClass {
  if (value === 'private_recovery') return 'audio_recovery';
  if (value === 'transcript_revision') return 'transcript';
  if (value === 'shared_reference') return 'visual_semantic';
  return 'control';
}

function isTrafficKind(value: unknown): value is SfuBroadcastDataTrafficKind {
  return ['interrupt', 'private_recovery', 'transcript_revision', 'control_hint', 'shared_reference']
    .includes(String(value));
}

function partition(values: readonly string[], size: number): readonly (readonly string[])[] {
  const batches: string[][] = [];
  for (let offset = 0; offset < values.length; offset += size) batches.push(values.slice(offset, offset + size));
  return batches;
}

function split(value: Uint8Array, size: number): readonly Uint8Array[] {
  const chunks: Uint8Array[] = [];
  for (let offset = 0; offset < value.byteLength; offset += size) chunks.push(value.slice(offset, offset + size));
  return chunks;
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', arrayBuffer(value));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = Uint8Array.from(value);
  return copy.buffer;
}

function identifier(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
}

function rejected(reasonCode: string): SfuBroadcastDataReceiveResult {
  return Object.freeze({ status: 'rejected' as const, reasonCode });
}

function reasonCode(error: unknown): string {
  return error instanceof Error && /^sfu_[A-Za-z0-9_]+$/.test(error.message)
    ? error.message : 'sfu_data_receive_failed';
}
