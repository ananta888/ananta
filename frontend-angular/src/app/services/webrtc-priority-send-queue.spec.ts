import {
  DataChannelSendPort,
  WebrtcPrioritySendQueue,
  WebrtcQueuePolicy,
} from './webrtc-priority-send-queue';

const POLICY: WebrtcQueuePolicy = {
  urgent: { maxMessages: 8, maxBytes: 4096, highWaterMark: 1000, lowWaterMark: 10, weight: 4 },
  interactive: { maxMessages: 8, maxBytes: 4096, highWaterMark: 700, lowWaterMark: 10, weight: 3 },
  live: { maxMessages: 4, maxBytes: 1024, highWaterMark: 300, lowWaterMark: 10, weight: 2 },
  bulk: { maxMessages: 2, maxBytes: 64, highWaterMark: 100, lowWaterMark: 10, weight: 1 },
};

class Channel implements DataChannelSendPort {
  bufferedAmount = 0;
  bufferedAmountLowThreshold = 0;
  readyState = 'closed';
  readonly sent: string[] = [];

  send(data: string): void { this.sent.push(data); }
}

describe('WebrtcPrioritySendQueue', () => {
  it('keeps control and transcript dispatchable while bulk is saturated', () => {
    let now = 1000;
    const queue = new WebrtcPrioritySendQueue(POLICY, () => now);
    const channel = new Channel();
    queue.bind(channel);
    expect(queue.enqueue('evidence_bulk', 'bulk-one', 5000)).toBe(true);
    expect(queue.enqueue('evidence_bulk', 'bulk-two', 5000)).toBe(true);
    expect(queue.enqueue('evidence_bulk', 'bulk-three', 5000)).toBe(true);
    expect(queue.enqueue('transcript', 'transcript', 5000)).toBe(true);
    expect(queue.enqueue('control', 'revoke', 5000)).toBe(true);

    now = 1010;
    channel.readyState = 'open';
    channel.bufferedAmount = 200;
    queue.flush();

    expect(channel.sent.slice(0, 2)).toEqual(['revoke', 'transcript']);
    expect(channel.sent).not.toContain('bulk-three');
    expect(queue.snapshot().drops['bulk_evicted']).toBe(1);
    expect(queue.p95Latency('urgent')).toBe(10);
  });

  it('drops expired bulk deterministically and exposes the reason without timers', () => {
    let now = 1000;
    const queue = new WebrtcPrioritySendQueue(POLICY, () => now);
    const channel = new Channel();
    queue.bind(channel);
    queue.enqueue('evidence_bulk', 'expired', 1001);
    now = 1002;
    channel.readyState = 'open';
    queue.flush();
    expect(channel.sent).toEqual([]);
    expect(queue.snapshot()).toMatchObject({ timers: 0, drops: { expired: 1 } });
  });

  it('rejects urgent overload explicitly instead of silently losing control', () => {
    const queue = new WebrtcPrioritySendQueue(POLICY, () => 1000);
    const channel = new Channel();
    queue.bind(channel);
    for (let index = 0; index < POLICY.urgent.maxMessages; index += 1) {
      expect(queue.enqueue('control', `control-${index}`, 5000)).toBe(true);
    }
    expect(queue.enqueue('control', 'overflow', 5000)).toBe(false);
    expect(queue.snapshot().drops['queue_overloaded']).toBe(1);
  });
});
