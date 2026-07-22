import { describe, expect, it } from 'vitest';

import { isAllowedSfuEndpoint } from './sfu-secure-endpoint.policy';

describe('isAllowedSfuEndpoint', () => {
  it('requires TLS and limits the explicit development exception to localhost', () => {
    expect(isAllowedSfuEndpoint('wss://sfu.example.test', 'websocket', false)).toBe(true);
    expect(isAllowedSfuEndpoint('ws://sfu.example.test', 'websocket', true)).toBe(false);
    expect(isAllowedSfuEndpoint('ws://localhost:7880', 'websocket', false)).toBe(false);
    expect(isAllowedSfuEndpoint('ws://localhost:7880', 'websocket', true)).toBe(true);
    expect(isAllowedSfuEndpoint('http://127.0.0.1:5000', 'http', true)).toBe(true);
    expect(isAllowedSfuEndpoint('http://10.0.0.5:5000', 'http', true)).toBe(false);
  });
});
