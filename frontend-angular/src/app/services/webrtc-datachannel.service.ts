/**
 * T20: DataChannel Protocol v1 — TypeScript port of datachannel_protocol.py
 * Supports core chat/view/cursor/control payloads and chunked transfer.
 */

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
  parts: string[];
  received: number;
}

const CHUNK_REASSEMBLY = new Map<string, DcChunkState>();

export function dcEncode(msg: DcMessage): string {
  const json = JSON.stringify(msg);
  if (json.length > DC_MAX_MESSAGE_BYTES) {
    throw new Error(`Message too large: ${json.length} > ${DC_MAX_MESSAGE_BYTES}`);
  }
  return json;
}

export function dcDecode(raw: string): DcMessage {
  if (raw.length > DC_MAX_MESSAGE_BYTES) {
    throw new Error(`Incoming message too large: ${raw.length}`);
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

export function dcTryReassembleChunk(msg: DcMessage): DcMessage | null {
  if (msg.type !== 'chunk') return msg;
  const chunkId = String(msg.payload['chunk_id'] || '');
  const index = Number(msg.payload['index']);
  const total = Number(msg.payload['total']);
  const data = String(msg.payload['data'] || '');
  if (!chunkId || !Number.isInteger(index) || !Number.isInteger(total) || total <= 0) return null;

  const state = CHUNK_REASSEMBLY.get(chunkId) ?? { total, parts: new Array(total).fill(''), received: 0 };
  if (!state.parts[index]) {
    state.parts[index] = data;
    state.received += 1;
  }
  CHUNK_REASSEMBLY.set(chunkId, state);

  if (state.received < state.total) return null;

  const assembled = state.parts.join('');
  CHUNK_REASSEMBLY.delete(chunkId);
  return dcDecode(assembled);
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
