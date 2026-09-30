import type {
  LegacyShareChatWireMessage,
  ShareChatMessage,
  StrictChatPlaintext,
  StrictShareChatWireMessage,
} from './share-session.types';

/**
 * Closed-schema codec of share-session chat: strict E2EE wire envelopes and
 * their decrypted plaintext, plus the tolerant legacy relay format. Pure;
 * sealing/opening and delivery stay in ShareSessionService.
 */

export function newShareChatMessageId(): string {
  return crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
}

export function createStrictChatPlaintext(
  id: string,
  sessionId: string,
  senderUserId: string,
  text: string,
  createdAt: number,
): StrictChatPlaintext {
  return {
    version: 1,
    id,
    sessionId,
    senderUserId,
    text,
    createdAt,
    visibility: 'room',
  };
}

export function chatMessageFromStrictPlaintext(plaintext: StrictChatPlaintext): ShareChatMessage {
  return {
    id: plaintext.id,
    session_id: plaintext.sessionId,
    sender_id: plaintext.senderUserId,
    text: plaintext.text,
    created_at: plaintext.createdAt,
    visibility: plaintext.visibility,
  };
}

export function parseStrictChatWire(raw: unknown): StrictShareChatWireMessage | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).length !== 2 || !('id' in value) || !('encrypted_payload' in value)) return null;
  if (typeof value['id'] !== 'string' || !value['id'] || value['id'].length > 96) return null;
  if (typeof value['encrypted_payload'] !== 'string' || !value['encrypted_payload']) return null;
  return { id: value['id'], encrypted_payload: value['encrypted_payload'] };
}

export function parseStrictChatPlaintext(raw: unknown): StrictChatPlaintext | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  const expected = ['version', 'id', 'sessionId', 'senderUserId', 'text', 'createdAt', 'visibility'];
  if (Object.keys(value).length !== expected.length || expected.some((key) => !(key in value))) return null;
  if (value['version'] !== 1 || value['visibility'] !== 'room') return null;
  if (typeof value['id'] !== 'string' || !value['id'] || value['id'].length > 96) return null;
  if (typeof value['sessionId'] !== 'string' || typeof value['senderUserId'] !== 'string') return null;
  if (typeof value['text'] !== 'string' || !value['text'].trim() || value['text'].length > 16_384) return null;
  if (typeof value['createdAt'] !== 'number' || !Number.isFinite(value['createdAt'])) return null;
  return value as unknown as StrictChatPlaintext;
}

/** Legacy relay message bound to the active session id, or null. */
export function parseLegacyChat(raw: unknown, activeSessionId: string | undefined): ShareChatMessage | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const value = raw as LegacyShareChatWireMessage;
  const text = typeof value.text === 'string' ? value.text : '';
  if (!text) return null;
  const sessionId = String(value.session_id ?? value.share_session_id ?? activeSessionId ?? '');
  if (!sessionId || sessionId !== activeSessionId) return null;
  return {
    id: String(value.id || `legacy-${Date.now()}`),
    session_id: sessionId,
    sender_id: String(value.sender_id ?? value.from_id ?? 'peer'),
    text,
    created_at: Number(value.created_at || Date.now() / 1000),
    visibility: String(value.visibility || 'room'),
  };
}
