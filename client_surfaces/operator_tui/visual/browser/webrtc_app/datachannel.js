/**
 * Ananta DataChannel Protocol v1 — JS implementation.
 * Must stay in sync with Python DataChannelProtocol in realtime/datachannel_protocol.py.
 *
 * Protocol VERSION = 1
 * MAX_MESSAGE_BYTES = 65536
 */

export const PROTOCOL_VERSION = 1;
export const MAX_MESSAGE_BYTES = 65536;
export const CHUNK_SIZE = 32768;

export const MESSAGE_TYPES = new Set([
  "hello",
  "hello_ack",
  "ping",
  "pong",
  "artifact_offer",
  "artifact_accept",
  "artifact_reject",
  "artifact_chunk",
  "artifact_complete",
  "error",
]);

/**
 * Encode a DataChannelMessage object to a UTF-8 string (sent over DataChannel as text).
 * @param {Object} msg
 * @returns {string}
 */
export function encode(msg) {
  const text = JSON.stringify(msg);
  if (text.length > MAX_MESSAGE_BYTES) {
    throw new Error(`Message too large: ${text.length} > ${MAX_MESSAGE_BYTES}`);
  }
  return text;
}

/**
 * Decode a UTF-8 string received over DataChannel.
 * @param {string|ArrayBuffer} data
 * @returns {Object}
 */
export function decode(data) {
  const text = typeof data === "string" ? data : new TextDecoder().decode(data);
  if (text.length > MAX_MESSAGE_BYTES) {
    throw new Error(`Incoming message too large: ${text.length} > ${MAX_MESSAGE_BYTES}`);
  }
  const msg = JSON.parse(text);
  if (msg.protocol_version !== PROTOCOL_VERSION) {
    throw new Error(`Protocol version mismatch: got ${msg.protocol_version}, expected ${PROTOCOL_VERSION}`);
  }
  if (!MESSAGE_TYPES.has(msg.type)) {
    throw new Error(`Unknown message type: ${msg.type}`);
  }
  return msg;
}

/** Generate a random message_id. */
function newMsgId() {
  return crypto.randomUUID();
}

/** Current UTC timestamp as float seconds. */
function now() {
  return Date.now() / 1000;
}

/**
 * Build a base DataChannelMessage structure.
 * @param {string} type
 * @param {string} sessionNonce
 * @param {Object} payload
 * @returns {Object}
 */
function makeMessage(type, sessionNonce, payload = {}) {
  return {
    type,
    protocol_version: PROTOCOL_VERSION,
    session_nonce: sessionNonce,
    message_id: newMsgId(),
    timestamp: now(),
    payload,
  };
}

export function makePing(sessionNonce) {
  return makeMessage("ping", sessionNonce);
}

export function makePong(pingMsg) {
  return makeMessage("pong", pingMsg.session_nonce, {
    ping_message_id: pingMsg.message_id,
    latency_ms: Math.round((now() - pingMsg.timestamp) * 1000),
  });
}

export function makeHello(sessionNonce, peerId) {
  return makeMessage("hello", sessionNonce, { peer_id: peerId });
}

export function makeHelloAck(helloMsg, peerId) {
  return makeMessage("hello_ack", helloMsg.session_nonce, {
    peer_id: peerId,
    hello_message_id: helloMsg.message_id,
  });
}

export function makeArtifactAccept(offerMsg) {
  return makeMessage("artifact_accept", offerMsg.session_nonce, {
    offer_id: offerMsg.payload.offer_id,
  });
}

export function makeArtifactReject(offerMsg, reason) {
  return makeMessage("artifact_reject", offerMsg.session_nonce, {
    offer_id: offerMsg.payload.offer_id,
    reason,
  });
}

/**
 * Set up DataChannel event handlers on an RTCDataChannel instance.
 *
 * @param {RTCDataChannel} dc - the data channel
 * @param {Object} handlers
 * @param {function(Object):void} handlers.onMessage - called with decoded message
 * @param {function():void} [handlers.onOpen]
 * @param {function():void} [handlers.onClose]
 * @param {function(Event):void} [handlers.onError]
 * @returns {RTCDataChannel} the same dc (for chaining)
 */
export function setupDataChannel(dc, handlers) {
  const { onMessage, onOpen, onClose, onError } = handlers || {};

  dc.onopen = () => {
    if (onOpen) onOpen();
  };

  dc.onclose = () => {
    if (onClose) onClose();
  };

  dc.onerror = (ev) => {
    if (onError) onError(ev);
  };

  dc.onmessage = (ev) => {
    try {
      const msg = decode(ev.data);
      if (onMessage) onMessage(msg);
    } catch (err) {
      if (onError) onError(err);
    }
  };

  return dc;
}
