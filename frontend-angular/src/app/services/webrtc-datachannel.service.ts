/**
 * T20: DataChannel Protocol v1 — TypeScript port of datachannel_protocol.py
 * Must stay in sync with Python implementation.
 */

export const DC_VERSION = 1;
export const DC_MAX_MESSAGE_BYTES = 65536;
export const DC_CHUNK_SIZE = 32768;

export type DcMessageType =
  | 'hello' | 'hello_ack' | 'ping' | 'pong'
  | 'artifact_offer' | 'artifact_accept' | 'artifact_reject'
  | 'artifact_chunk' | 'artifact_complete' | 'error';

export const DC_MESSAGE_TYPES = new Set<DcMessageType>([
  'hello', 'hello_ack', 'ping', 'pong',
  'artifact_offer', 'artifact_accept', 'artifact_reject',
  'artifact_chunk', 'artifact_complete', 'error',
]);

export interface DcMessage {
  type: DcMessageType;
  protocol_version: number;
  session_nonce: string;
  message_id: string;
  timestamp: number;
  payload: Record<string, unknown>;
}

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
