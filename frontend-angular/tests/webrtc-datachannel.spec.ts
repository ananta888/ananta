import { test, expect } from '@playwright/test';
import { dcMake, dcEncode, dcDecode, dcEncodeChunked, dcTryReassembleChunk } from '../src/app/services/webrtc-datachannel.service';

test.describe('WebRTC DataChannel Protocol', () => {
  test('encodes/decodes standard messages', async () => {
    const msg = dcMake('chat', 'nonce-1', { text: 'hello' });
    const encoded = dcEncode(msg);
    const decoded = dcDecode(encoded);
    expect(decoded.type).toBe('chat');
    expect(decoded.payload['text']).toBe('hello');
  });

  test('reassembles chunked payloads', async () => {
    const large = 'x'.repeat(50_000);
    const base = dcMake('view_payload', 'nonce-2', { body: large });
    const chunks = dcEncodeChunked(base);
    expect(chunks.length).toBeGreaterThan(1);

    let out: any = null;
    for (const chunk of chunks) {
      out = dcTryReassembleChunk(chunk);
      if (out) break;
    }

    expect(out).toBeTruthy();
    expect(out.type).toBe('view_payload');
    expect(out.payload['body']).toBe(large);
  });
});
