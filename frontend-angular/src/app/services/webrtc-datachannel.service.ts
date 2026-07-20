/**
 * T20: DataChannel Protocol v1 — TypeScript port of datachannel_protocol.py
 * Supports core chat/view/cursor/control payloads and chunked transfer.
 */
import type { BoundedChunk } from './webrtc-chunk-reassembly.store';

export const DC_VERSION = 1;
export const DC_MAX_MESSAGE_BYTES = 65536;
export const DC_CHUNK_SIZE = 32768;

export type DcMessageType =
  | 'hello' | 'hello_ack' | 'ping' | 'pong'
  | 'chat' | 'view_payload' | 'cursor' | 'artifact' | 'control'
  | 'artifact_offer' | 'artifact_accept' | 'artifact_reject'
  | 'artifact_chunk' | 'artifact_complete' | 'chunk' | 'error';

export const DC_MESSAGE_TYPES = new Set<DcMessageType>([
  'hello', 'hello_ack', 'ping', 'pong',
  'chat', 'view_payload', 'cursor', 'artifact', 'control',
  'artifact_offer', 'artifact_accept', 'artifact_reject',
  'artifact_chunk', 'artifact_complete', 'chunk', 'error',
]);

export interface DcMessage {
  type: DcMessageType;
  protocol_version: number;
  session_nonce: string;
  message_id: string;
  timestamp: number;
  payload: Record<string, unknown>;
}

export interface DcChunkState {
  total: number;
  parts: Map<number, string>;
  received: number;
  bytes: number;
  expiresAt: number;
}

export class DcLegacyChunkReassembler {
  private readonly states = new Map<string, DcChunkState>();

  constructor(
    private readonly maxStates = 64,
    private readonly maxBytes = 4 * DC_MAX_MESSAGE_BYTES,
    private readonly ttlMs = 60_000,
  ) {}

  accept(msg: DcMessage, nowMs = Date.now()): DcMessage | null {
    if (msg.type !== 'chunk') return msg;
    this.expire(nowMs);
    const chunkId = String(msg.payload['chunk_id'] || '');
    const index = Number(msg.payload['index']);
    const total = Number(msg.payload['total']);
    const data = String(msg.payload['data'] || '');
    if (
      !chunkId || !Number.isSafeInteger(index) || !Number.isSafeInteger(total)
      || total <= 0 || total > 256 || index < 0 || index >= total
      || data.length > DC_CHUNK_SIZE
    ) return null;
    const key = `${msg.session_nonce}\x1f${chunkId}`;
    let state = this.states.get(key);
    if (!state) {
      this.evictToFit(data.length);
      if (this.states.size >= this.maxStates || this.bytes() + data.length > this.maxBytes) return null;
      state = { total, parts: new Map(), received: 0, bytes: 0, expiresAt: nowMs + this.ttlMs };
      this.states.set(key, state);
    }
    if (state.total !== total) {
      this.states.delete(key);
      return null;
    }
    const prior = state.parts.get(index);
    if (prior !== undefined) {
      if (prior !== data) this.states.delete(key);
      return null;
    }
    state.parts.set(index, data);
    state.received += 1;
    state.bytes += data.length;
    if (state.bytes > DC_MAX_MESSAGE_BYTES) {
      this.states.delete(key);
      return null;
    }
    if (state.received < state.total) return null;
    let assembled = '';
    for (let partIndex = 0; partIndex < state.total; partIndex += 1) {
      const part = state.parts.get(partIndex);
      if (part === undefined) {
        this.states.delete(key);
        return null;
      }
      assembled += part;
    }
    this.states.delete(key);
    return dcDecode(assembled);
  }

  clear(): void { this.states.clear(); }

  snapshot(): Readonly<{ states: number; bytes: number; timers: number }> {
    return Object.freeze({ states: this.states.size, bytes: this.bytes(), timers: 0 });
  }

  private expire(nowMs: number): void {
    for (const [key, state] of this.states) {
      if (state.expiresAt <= nowMs) this.states.delete(key);
    }
  }

  private evictToFit(incomingBytes: number): void {
    while (this.states.size >= this.maxStates || this.bytes() + incomingBytes > this.maxBytes) {
      const oldest = this.states.keys().next().value as string | undefined;
      if (oldest === undefined) return;
      this.states.delete(oldest);
    }
  }

  private bytes(): number {
    return Array.from(this.states.values()).reduce((sum, state) => sum + state.bytes, 0);
  }
}

export function dcEncode(msg: DcMessage): string {
  const json = JSON.stringify(msg);
  const bytes = new TextEncoder().encode(json).byteLength;
  if (bytes > DC_MAX_MESSAGE_BYTES) {
    throw new Error(`Message too large: ${bytes} > ${DC_MAX_MESSAGE_BYTES}`);
  }
  return json;
}

export function dcDecode(raw: string): DcMessage {
  if (raw.length > DC_MAX_MESSAGE_BYTES) {
    throw new Error(`Incoming message too large: ${raw.length}`);
  }
  if (new TextEncoder().encode(raw).byteLength > DC_MAX_MESSAGE_BYTES) {
    throw new Error('Incoming message too large');
  }
  const msg = JSON.parse(raw) as DcMessage;
  if (msg.protocol_version !== DC_VERSION) {
    throw new Error(`Unsupported protocol version: ${msg.protocol_version}`);
  }
  if (!DC_MESSAGE_TYPES.has(msg.type)) {
    throw new Error(`Unknown message type: ${msg.type}`);
  }
  return msg;
}

export function dcEncodeChunked(msg: DcMessage): DcMessage[] {
  const raw = JSON.stringify(msg);
  if (raw.length <= DC_CHUNK_SIZE) return [msg];

  const chunkId = msg.message_id;
  const total = Math.ceil(raw.length / DC_CHUNK_SIZE);
  if (total > 256 || new TextEncoder().encode(raw).byteLength > DC_MAX_MESSAGE_BYTES) {
    throw new Error('Chunked message exceeds bounded legacy budget');
  }
  const out: DcMessage[] = [];
  for (let i = 0; i < total; i += 1) {
    const start = i * DC_CHUNK_SIZE;
    const end = start + DC_CHUNK_SIZE;
    const fragment = raw.slice(start, end);
    out.push(dcMake('chunk', msg.session_nonce, {
      chunk_id: chunkId,
      index: i,
      total,
      data: fragment,
      wrapped_type: msg.type,
    }));
  }
  return out;
}

export function dcTryReassembleChunk(
  msg: DcMessage,
  reassembler: DcLegacyChunkReassembler,
): DcMessage | null {
  return reassembler.accept(msg);
}

export function dcMake(
  type: DcMessageType,
  nonce: string,
  payload: Record<string, unknown> = {},
): DcMessage {
  return {
    type,
    protocol_version: DC_VERSION,
    session_nonce: nonce,
    message_id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
    timestamp: Date.now() / 1000,
    payload,
  };
}

export const SEMANTIC_DC_VERSION = 'ananta.webrtc-datachannel.v1' as const;
export const SEMANTIC_DC_MAX_WIRE_BYTES = 1_500_000;
export type SemanticTrafficClass =
  | 'control' | 'transcript' | 'audio_recovery'
  | 'visual_semantic' | 'evidence_bulk' | 'diagnostic';

export const SEMANTIC_TRAFFIC_CLASS_LIMITS: Readonly<Record<SemanticTrafficClass, number>> = Object.freeze({
  control: 16_384,
  transcript: 65_536,
  audio_recovery: 262_144,
  visual_semantic: 524_288,
  evidence_bulk: 1_048_576,
  diagnostic: 8_192,
});

export interface SemanticDataChannelMessage {
  version: typeof SEMANTIC_DC_VERSION;
  traffic_class: SemanticTrafficClass;
  message_id: string;
  session_id: string;
  epoch: number;
  sender_id: string;
  audience_id: string;
  sequence: number;
  expires_at_ms: number;
  compression: 'none';
  security: { algorithm: 'AES-GCM-256'; key_id: string };
  payload_bytes: number;
  payload_digest: string;
  ciphertext: string;
}

export class SemanticDataChannelError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

const SEMANTIC_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const SEMANTIC_DIGEST_RE = /^[0-9a-f]{64}$/;
const SEMANTIC_B64_RE = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
const SEMANTIC_FRAME_RE = /^ANANTA-DC1 ([a-z_]+) ([0-9]+) ([0-9]+)$/;
const SEMANTIC_CHUNK_FRAME_RE = /^ANANTA-DCCHUNK1 ([a-z_]+) ([0-9]+) ([0-9]+)$/;
const SEMANTIC_CHUNK_FIELDS = [
  'version', 'chunk_id', 'message_id', 'session_id', 'epoch', 'sender_id',
  'traffic_class', 'index', 'total', 'chunk_bytes', 'total_bytes',
  'expires_at_ms', 'payload_digest', 'data',
] as const;
export const SEMANTIC_DC_CHUNK_BYTES = 48 * 1024;

export async function semanticDcEncode(message: SemanticDataChannelMessage): Promise<string> {
  const validated = await validateSemanticDcMessage(message);
  const body = JSON.stringify(validated);
  const bodyBytes = new TextEncoder().encode(body).byteLength;
  const frame = `ANANTA-DC1 ${validated.traffic_class} ${validated.payload_bytes} ${bodyBytes}\n${body}`;
  if (new TextEncoder().encode(frame).byteLength > SEMANTIC_DC_MAX_WIRE_BYTES) {
    throw new SemanticDataChannelError('wire_message_too_large');
  }
  return frame;
}

export async function semanticDcDecode(raw: string): Promise<SemanticDataChannelMessage> {
  if (typeof raw !== 'string' || raw.length > SEMANTIC_DC_MAX_WIRE_BYTES) {
    throw new SemanticDataChannelError('wire_message_too_large');
  }
  const separator = raw.indexOf('\n');
  if (separator < 1 || separator > 96) throw new SemanticDataChannelError('wire_header_invalid');
  const match = SEMANTIC_FRAME_RE.exec(raw.slice(0, separator));
  if (!match) throw new SemanticDataChannelError('wire_header_invalid');
  const trafficClass = match[1] as SemanticTrafficClass;
  const classLimit = SEMANTIC_TRAFFIC_CLASS_LIMITS[trafficClass];
  if (classLimit === undefined) throw new SemanticDataChannelError('unknown_traffic_class');
  const declaredPayloadBytes = strictUnsignedInteger(match[2], 'wire_payload_bytes_invalid');
  const declaredBodyBytes = strictUnsignedInteger(match[3], 'wire_body_bytes_invalid');
  if (declaredPayloadBytes > classLimit) throw new SemanticDataChannelError('payload_too_large');
  if (declaredBodyBytes > SEMANTIC_DC_MAX_WIRE_BYTES) throw new SemanticDataChannelError('wire_message_too_large');
  const body = raw.slice(separator + 1);
  if (new TextEncoder().encode(body).byteLength !== declaredBodyBytes) {
    throw new SemanticDataChannelError('wire_body_size_mismatch');
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    throw new SemanticDataChannelError('invalid_json');
  }
  const validated = await validateSemanticDcMessage(parsed);
  if (validated.traffic_class !== trafficClass || validated.payload_bytes !== declaredPayloadBytes) {
    throw new SemanticDataChannelError('wire_header_mismatch');
  }
  return validated;
}

export interface SemanticEncodedPackets {
  readonly digest: string;
  readonly packets: readonly string[];
  readonly chunked: boolean;
}

export async function semanticDcEncodePackets(
  message: SemanticDataChannelMessage,
): Promise<SemanticEncodedPackets> {
  const frame = await semanticDcEncode(message);
  const bytes = new TextEncoder().encode(frame);
  const digest = await sha256Hex(bytes);
  if (bytes.byteLength <= SEMANTIC_DC_CHUNK_BYTES) {
    return Object.freeze({ digest, packets: Object.freeze([frame]), chunked: false });
  }
  const total = Math.ceil(bytes.byteLength / SEMANTIC_DC_CHUNK_BYTES);
  if (total > 256) throw new SemanticDataChannelError('chunk_total_exceeded');
  const chunkId = await sha256Hex(new TextEncoder().encode(
    `${message.session_id}\n${message.epoch}\n${message.sender_id}\n${digest}`,
  ));
  const packets: string[] = [];
  for (let index = 0; index < total; index += 1) {
    const part = bytes.slice(index * SEMANTIC_DC_CHUNK_BYTES, (index + 1) * SEMANTIC_DC_CHUNK_BYTES);
    const chunk: BoundedChunk = {
      version: 'ananta.webrtc-bounded-chunk.v1',
      chunk_id: chunkId,
      message_id: message.message_id,
      session_id: message.session_id,
      epoch: message.epoch,
      sender_id: message.sender_id,
      traffic_class: message.traffic_class,
      index,
      total,
      chunk_bytes: part.byteLength,
      total_bytes: bytes.byteLength,
      expires_at_ms: message.expires_at_ms,
      payload_digest: digest,
      data: encodeSemanticBase64(part),
    };
    const body = JSON.stringify(chunk);
    const bodyBytes = new TextEncoder().encode(body).byteLength;
    packets.push(`ANANTA-DCCHUNK1 ${message.traffic_class} ${part.byteLength} ${bodyBytes}\n${body}`);
  }
  return Object.freeze({ digest, packets: Object.freeze(packets), chunked: true });
}

export function semanticDcDecodeChunk(raw: string): BoundedChunk {
  if (typeof raw !== 'string' || raw.length > 400_000) {
    throw new SemanticDataChannelError('chunk_wire_too_large');
  }
  const separator = raw.indexOf('\n');
  if (separator < 1 || separator > 96) throw new SemanticDataChannelError('chunk_header_invalid');
  const match = SEMANTIC_CHUNK_FRAME_RE.exec(raw.slice(0, separator));
  if (!match) throw new SemanticDataChannelError('chunk_header_invalid');
  const trafficClass = match[1] as SemanticTrafficClass;
  if (SEMANTIC_TRAFFIC_CLASS_LIMITS[trafficClass] === undefined) {
    throw new SemanticDataChannelError('unknown_traffic_class');
  }
  const declaredChunkBytes = strictUnsignedInteger(match[2], 'chunk_bytes_invalid');
  const declaredBodyBytes = strictUnsignedInteger(match[3], 'chunk_body_bytes_invalid');
  if (declaredChunkBytes > 262_144 || declaredBodyBytes > 400_000) {
    throw new SemanticDataChannelError('chunk_wire_too_large');
  }
  const body = raw.slice(separator + 1);
  if (new TextEncoder().encode(body).byteLength !== declaredBodyBytes) {
    throw new SemanticDataChannelError('chunk_body_size_mismatch');
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    throw new SemanticDataChannelError('invalid_json');
  }
  const value = closedSemanticObject(parsed, SEMANTIC_CHUNK_FIELDS);
  if (
    value['traffic_class'] !== trafficClass
    || value['chunk_bytes'] !== declaredChunkBytes
  ) throw new SemanticDataChannelError('chunk_header_mismatch');
  return value as unknown as BoundedChunk;
}

export async function validateSemanticDcMessage(raw: unknown): Promise<SemanticDataChannelMessage> {
  const keys = [
    'version', 'traffic_class', 'message_id', 'session_id', 'epoch', 'sender_id',
    'audience_id', 'sequence', 'expires_at_ms', 'compression', 'security',
    'payload_bytes', 'payload_digest', 'ciphertext',
  ] as const;
  const value = closedSemanticObject(raw, keys);
  if (value['version'] !== SEMANTIC_DC_VERSION) throw new SemanticDataChannelError('unsupported_version');
  const trafficClass = value['traffic_class'] as SemanticTrafficClass;
  const classLimit = SEMANTIC_TRAFFIC_CLASS_LIMITS[trafficClass];
  if (classLimit === undefined) throw new SemanticDataChannelError('unknown_traffic_class');
  if (value['compression'] !== 'none') throw new SemanticDataChannelError('unsupported_compression');
  const security = closedSemanticObject(value['security'], ['algorithm', 'key_id']);
  if (security['algorithm'] !== 'AES-GCM-256') {
    throw new SemanticDataChannelError('unsupported_security_algorithm');
  }
  const payloadBytes = boundedSemanticInteger(value['payload_bytes'], 0, classLimit, 'payload_too_large');
  if (typeof value['ciphertext'] !== 'string' || value['ciphertext'].length > Math.ceil(payloadBytes / 3) * 4) {
    throw new SemanticDataChannelError('payload_too_large');
  }
  const ciphertext = decodeSemanticBase64(value['ciphertext']);
  if (ciphertext.byteLength !== payloadBytes) throw new SemanticDataChannelError('payload_size_mismatch');
  const digest = identifierPattern(value['payload_digest'], SEMANTIC_DIGEST_RE, 'invalid_digest');
  if (await sha256Hex(ciphertext) !== digest) throw new SemanticDataChannelError('payload_digest_mismatch');
  return {
    version: SEMANTIC_DC_VERSION,
    traffic_class: trafficClass,
    message_id: semanticIdentifier(value['message_id']),
    session_id: semanticIdentifier(value['session_id']),
    epoch: boundedSemanticInteger(value['epoch'], 1, Number.MAX_SAFE_INTEGER, 'invalid_integer'),
    sender_id: semanticIdentifier(value['sender_id']),
    audience_id: semanticIdentifier(value['audience_id']),
    sequence: boundedSemanticInteger(value['sequence'], 1, Number.MAX_SAFE_INTEGER, 'invalid_integer'),
    expires_at_ms: boundedSemanticInteger(value['expires_at_ms'], 1, Number.MAX_SAFE_INTEGER, 'invalid_integer'),
    compression: 'none',
    security: {
      algorithm: 'AES-GCM-256',
      key_id: semanticIdentifier(security['key_id']),
    },
    payload_bytes: payloadBytes,
    payload_digest: digest,
    ciphertext: encodeSemanticBase64(ciphertext),
  };
}

function closedSemanticObject(raw: unknown, allowed: readonly string[]): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new SemanticDataChannelError('invalid_object');
  const value = raw as Record<string, unknown>;
  const keys = Object.keys(value);
  if (keys.some(key => !allowed.includes(key))) throw new SemanticDataChannelError('unknown_field');
  if (allowed.some(key => !(key in value))) throw new SemanticDataChannelError('required_field_missing');
  return value;
}

function semanticIdentifier(value: unknown): string {
  return identifierPattern(value, SEMANTIC_ID_RE, 'invalid_identifier');
}

function identifierPattern(value: unknown, pattern: RegExp, reason: string): string {
  if (typeof value !== 'string' || !pattern.test(value)) throw new SemanticDataChannelError(reason);
  return value;
}

function boundedSemanticInteger(value: unknown, minimum: number, maximum: number, reason: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    throw new SemanticDataChannelError(reason);
  }
  return value as number;
}

function strictUnsignedInteger(value: string, reason: string): number {
  if (!/^(?:0|[1-9][0-9]{0,15})$/.test(value)) throw new SemanticDataChannelError(reason);
  return boundedSemanticInteger(Number(value), 0, Number.MAX_SAFE_INTEGER, reason);
}

function decodeSemanticBase64(value: string): Uint8Array {
  if (!SEMANTIC_B64_RE.test(value)) throw new SemanticDataChannelError('invalid_base64');
  try {
    return Uint8Array.from(atob(value), character => character.charCodeAt(0));
  } catch {
    throw new SemanticDataChannelError('invalid_base64');
  }
}

function encodeSemanticBase64(value: Uint8Array): string {
  let binary = '';
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const owned = Uint8Array.from(value);
  const digest = await crypto.subtle.digest('SHA-256', owned.buffer);
  return Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, '0')).join('');
}
