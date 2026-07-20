import goldenCases from '../../../../tests/fixtures/webrtc/datachannel_message_cases.json';

import {
  DcLegacyChunkReassembler,
  SEMANTIC_DC_VERSION,
  SEMANTIC_TRAFFIC_CLASS_LIMITS,
  SemanticDataChannelError,
  SemanticDataChannelMessage,
  dcEncodeChunked,
  dcMake,
  dcTryReassembleChunk,
  semanticDcDecodeChunk,
  semanticDcDecode,
  semanticDcEncode,
  semanticDcEncodePackets,
} from './webrtc-datachannel.service';
import { WebrtcChunkReassemblyStore } from './webrtc-chunk-reassembly.store';

async function message(payload: Uint8Array, trafficClass = 'control'): Promise<SemanticDataChannelMessage> {
  const digest = await crypto.subtle.digest('SHA-256', Uint8Array.from(payload).buffer);
  const digestHex = Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, '0')).join('');
  let binary = '';
  for (const byte of payload) binary += String.fromCharCode(byte);
  return {
    version: SEMANTIC_DC_VERSION,
    traffic_class: trafficClass as SemanticDataChannelMessage['traffic_class'],
    message_id: 'message-1',
    session_id: 'session-1',
    epoch: 1,
    sender_id: 'sender-1',
    audience_id: 'receiver-1',
    sequence: 1,
    expires_at_ms: 10_000,
    compression: 'none',
    security: { algorithm: 'AES-GCM-256', key_id: 'key-1' },
    payload_bytes: payload.byteLength,
    payload_digest: digestHex,
    ciphertext: btoa(binary),
  };
}

describe('semantic DataChannel contract', () => {
  it('validates the same deterministic golden cases as Python', async () => {
    for (const golden of goldenCases) {
      const payload = Uint8Array.from(golden.payload_hex.match(/.{2}/g) ?? [], value => Number.parseInt(value, 16));
      const candidate = await message(payload, golden.traffic_class);
      if (golden.expected === 'accept') {
        const parsed = await semanticDcDecode(await semanticDcEncode(candidate));
        expect(parsed.traffic_class, golden.name).toBe(golden.traffic_class);
      } else {
        await expect(semanticDcEncode(candidate)).rejects.toMatchObject({ reasonCode: golden.expected });
      }
    }
  });

  it('checks dispatch type and declared bytes before parsing JSON', async () => {
    await expect(semanticDcDecode('ANANTA-DC1 evidence_bulk 1048577 1\n{'))
      .rejects.toMatchObject({ reasonCode: 'payload_too_large' });
    await expect(semanticDcDecode('ANANTA-DC1 generic 1 1\n{'))
      .rejects.toMatchObject({ reasonCode: 'unknown_traffic_class' });
  });

  it('accepts exact class maxima and rejects one byte more', async () => {
    const maximum = SEMANTIC_TRAFFIC_CLASS_LIMITS.diagnostic;
    await expect(semanticDcEncode(await message(new Uint8Array(maximum), 'diagnostic'))).resolves.toContain(
      'ANANTA-DC1 diagnostic',
    );
    await expect(semanticDcEncode(await message(new Uint8Array(maximum + 1), 'diagnostic')))
      .rejects.toBeInstanceOf(SemanticDataChannelError);
  });

  it('chunks a large frame and reassembles it within the declared context', async () => {
    const original = await message(new Uint8Array(60_000), 'transcript');
    const encoded = await semanticDcEncodePackets(original);
    expect(encoded.chunked).toBe(true);
    const store = new WebrtcChunkReassemblyStore();
    let completed: Uint8Array | undefined;
    for (const packet of [...encoded.packets].reverse()) {
      const result = await store.accept(semanticDcDecodeChunk(packet), 1000);
      if (result.status === 'complete') completed = result.value;
    }
    expect(completed).toBeDefined();
    const decoded = await semanticDcDecode(new TextDecoder('utf-8', { fatal: true }).decode(completed));
    expect(decoded.payload_digest).toBe(original.payload_digest);
    expect(store.snapshot()).toEqual({ states: 0, bytes: 0, timers: 0 });
  });
});

describe('bounded legacy chunk compatibility', () => {
  it('keeps chunk state session-local and leaves no timers after dispatch', () => {
    const value = dcMake('artifact', 'session-nonce', { value: 'x'.repeat(40_000) });
    const chunks = dcEncodeChunked(value);
    const left = new DcLegacyChunkReassembler();
    const right = new DcLegacyChunkReassembler();
    expect(dcTryReassembleChunk(chunks[0], left)).toBeNull();
    expect(dcTryReassembleChunk(chunks[1], right)).toBeNull();
    expect(right.snapshot().states).toBe(1);
    expect(dcTryReassembleChunk(chunks[1], left)).toEqual(value);
    expect(left.snapshot()).toEqual({ states: 0, bytes: 0, timers: 0 });
  });
});
