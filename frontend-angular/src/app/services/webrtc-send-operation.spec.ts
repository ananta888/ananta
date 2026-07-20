import { vi } from 'vitest';

import { WebrtcSendOperation } from './webrtc-send-operation';

const digest = 'a'.repeat(64);

describe('WebrtcSendOperation', () => {
  it('settles exactly once when cancelled before or during send', async () => {
    const controller = new AbortController();
    const operation = new WebrtcSendOperation('session', 1, digest, Date.now() + 1000, controller.signal);
    controller.abort();
    operation.acknowledge(9);
    expect((await operation.result).state).toBe('cancelled');
  });

  it('accepts a monotone ack and ignores late duplicate effects', async () => {
    const operation = new WebrtcSendOperation('session', 1, digest, Date.now() + 1000);
    operation.markSending();
    operation.acknowledge(5);
    operation.acknowledge(4);
    operation.disconnect();
    expect(await operation.result).toEqual({
      sessionId: 'session', epoch: 1, messageDigest: digest, state: 'acknowledged', ackCursor: 5,
    });
  });

  it('cleans its timer on timeout', async () => {
    vi.useFakeTimers();
    try {
      const operation = new WebrtcSendOperation('session', 1, digest, Date.now() + 10);
      await vi.advanceTimersByTimeAsync(11);
      expect((await operation.result).state).toBe('timed_out');
    } finally {
      vi.useRealTimers();
    }
  });
});
