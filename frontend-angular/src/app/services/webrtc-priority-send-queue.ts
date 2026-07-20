import { WebrtcSendOperation } from './webrtc-send-operation';
import type { SemanticTrafficClass } from './webrtc-datachannel.service';

export type WebrtcQueueLane = 'urgent' | 'interactive' | 'live' | 'bulk';

export interface WebrtcQueueLanePolicy {
  maxMessages: number;
  maxBytes: number;
  highWaterMark: number;
  lowWaterMark: number;
  weight: number;
}

export type WebrtcQueuePolicy = Readonly<Record<WebrtcQueueLane, WebrtcQueueLanePolicy>>;

export const DEFAULT_WEBRTC_QUEUE_POLICY: WebrtcQueuePolicy = Object.freeze({
  urgent: Object.freeze({ maxMessages: 256, maxBytes: 2_097_152, highWaterMark: 2_097_152, lowWaterMark: 131_072, weight: 8 }),
  interactive: Object.freeze({ maxMessages: 512, maxBytes: 8_388_608, highWaterMark: 1_048_576, lowWaterMark: 131_072, weight: 6 }),
  live: Object.freeze({ maxMessages: 128, maxBytes: 8_388_608, highWaterMark: 524_288, lowWaterMark: 65_536, weight: 3 }),
  bulk: Object.freeze({ maxMessages: 64, maxBytes: 16_777_216, highWaterMark: 262_144, lowWaterMark: 32_768, weight: 1 }),
});

export interface DataChannelSendPort {
  bufferedAmount: number;
  bufferedAmountLowThreshold: number;
  readyState: string;
  send(data: string): void;
}

interface QueueItem {
  readonly lane: WebrtcQueueLane;
  readonly payload: string;
  readonly bytes: number;
  readonly expiresAtMs: number;
  readonly queuedAtMs: number;
  readonly operation?: WebrtcSendOperation;
  readonly ordinal: number;
}

export interface QueueSnapshot {
  messages: Readonly<Record<WebrtcQueueLane, number>>;
  bytes: Readonly<Record<WebrtcQueueLane, number>>;
  timers: 0;
  drops: Readonly<Record<string, number>>;
}

const LANES: readonly WebrtcQueueLane[] = ['urgent', 'interactive', 'live', 'bulk'];

export class WebrtcPrioritySendQueue {
  private readonly queues = new Map<WebrtcQueueLane, QueueItem[]>(LANES.map(lane => [lane, []]));
  private readonly drops = new Map<string, number>();
  private readonly latencies = new Map<WebrtcQueueLane, number[]>(LANES.map(lane => [lane, []]));
  private channel: DataChannelSendPort | null = null;
  private ordinal = 0;

  constructor(
    private readonly policy: WebrtcQueuePolicy = DEFAULT_WEBRTC_QUEUE_POLICY,
    private readonly clock: () => number = () => Date.now(),
  ) {
    this.validatePolicy();
  }

  bind(channel: DataChannelSendPort): void {
    this.channel = channel;
    channel.bufferedAmountLowThreshold = Math.min(...LANES.map(lane => this.policy[lane].lowWaterMark));
    this.flush();
  }

  unbind(): void {
    this.channel = null;
    this.finishAndClear('disconnect');
  }

  enqueue(
    trafficClass: SemanticTrafficClass,
    payload: string,
    expiresAtMs: number,
    operation?: WebrtcSendOperation,
  ): boolean {
    const now = this.clock();
    const lane = laneForTrafficClass(trafficClass);
    const bytes = new TextEncoder().encode(payload).byteLength;
    if (!Number.isSafeInteger(expiresAtMs) || expiresAtMs <= now) {
      this.drop('expired', operation);
      return false;
    }
    this.purgeExpired(now);
    const queue = this.queues.get(lane)!;
    const lanePolicy = this.policy[lane];
    const existingBytes = queue.reduce((sum, item) => sum + item.bytes, 0);
    if (queue.length >= lanePolicy.maxMessages || existingBytes + bytes > lanePolicy.maxBytes) {
      if (lane === 'bulk') this.evictBulkToFit(bytes);
    }
    const boundedQueue = this.queues.get(lane)!;
    const boundedBytes = boundedQueue.reduce((sum, item) => sum + item.bytes, 0);
    if (boundedQueue.length >= lanePolicy.maxMessages || boundedBytes + bytes > lanePolicy.maxBytes) {
      this.drop('queue_overloaded', operation);
      return false;
    }
    const item: QueueItem = {
      lane,
      payload,
      bytes,
      expiresAtMs,
      queuedAtMs: now,
      operation,
      ordinal: ++this.ordinal,
    };
    boundedQueue.push(item);
    if (operation) {
      void operation.result.then(() => this.removeOperation(operation));
    }
    this.flush();
    return true;
  }

  flush(): number {
    const channel = this.channel;
    if (!channel || channel.readyState !== 'open') return 0;
    const now = this.clock();
    this.purgeExpired(now);
    let sent = 0;
    let progressed = true;
    while (progressed) {
      progressed = false;
      for (const lane of LANES) {
        const queue = this.queues.get(lane)!;
        const lanePolicy = this.policy[lane];
        let allowance = lanePolicy.weight;
        while (queue.length && allowance > 0 && channel.bufferedAmount <= lanePolicy.highWaterMark) {
          const item = queue.shift()!;
          if (item.expiresAtMs <= now) {
            this.drop('expired', item.operation);
            continue;
          }
          try {
            item.operation?.markSending();
            channel.send(item.payload);
          } catch {
            item.operation?.disconnect();
            this.incrementDrop('send_failed');
            continue;
          }
          this.recordLatency(lane, Math.max(0, now - item.queuedAtMs));
          allowance -= 1;
          sent += 1;
          progressed = true;
        }
      }
    }
    return sent;
  }

  cancelContext(sessionId: string, epoch?: number): void {
    for (const lane of LANES) {
      const queue = this.queues.get(lane)!;
      for (const item of queue) {
        if (item.operation?.sessionId !== sessionId) continue;
        if (epoch !== undefined && item.operation.epoch !== epoch) continue;
        item.operation.disconnect();
      }
      this.queues.set(lane, queue.filter(item => {
        if (item.operation?.sessionId !== sessionId) return true;
        return epoch !== undefined && item.operation.epoch !== epoch;
      }));
    }
  }

  p95Latency(lane: WebrtcQueueLane): number {
    const values = [...this.latencies.get(lane)!].sort((left, right) => left - right);
    if (!values.length) return 0;
    return values[Math.max(0, Math.ceil(values.length * 0.95) - 1)];
  }

  snapshot(): QueueSnapshot {
    const messages = {} as Record<WebrtcQueueLane, number>;
    const bytes = {} as Record<WebrtcQueueLane, number>;
    for (const lane of LANES) {
      const queue = this.queues.get(lane)!;
      messages[lane] = queue.length;
      bytes[lane] = queue.reduce((sum, item) => sum + item.bytes, 0);
    }
    return Object.freeze({
      messages: Object.freeze(messages),
      bytes: Object.freeze(bytes),
      timers: 0,
      drops: Object.freeze(Object.fromEntries(this.drops)),
    });
  }

  private purgeExpired(now: number): void {
    for (const lane of LANES) {
      const retained: QueueItem[] = [];
      for (const item of this.queues.get(lane)!) {
        if (item.expiresAtMs <= now) this.drop('expired', item.operation);
        else retained.push(item);
      }
      this.queues.set(lane, retained);
    }
  }

  private evictBulkToFit(incomingBytes: number): void {
    const queue = this.queues.get('bulk')!;
    const policy = this.policy.bulk;
    queue.sort((left, right) => left.expiresAtMs - right.expiresAtMs || left.ordinal - right.ordinal);
    while (
      queue.length >= policy.maxMessages
      || queue.reduce((sum, item) => sum + item.bytes, 0) + incomingBytes > policy.maxBytes
    ) {
      const removed = queue.shift();
      if (!removed) return;
      this.drop('bulk_evicted', removed.operation);
    }
  }

  private removeOperation(operation: WebrtcSendOperation): void {
    for (const lane of LANES) {
      this.queues.set(lane, this.queues.get(lane)!.filter(item => item.operation !== operation));
    }
  }

  private finishAndClear(reason: 'disconnect'): void {
    for (const lane of LANES) {
      for (const item of this.queues.get(lane)!) item.operation?.disconnect();
      this.queues.set(lane, []);
    }
    this.incrementDrop(reason);
  }

  private drop(reason: string, operation?: WebrtcSendOperation): void {
    operation?.cancel();
    this.incrementDrop(reason);
  }

  private incrementDrop(reason: string): void {
    this.drops.set(reason, (this.drops.get(reason) ?? 0) + 1);
  }

  private recordLatency(lane: WebrtcQueueLane, value: number): void {
    const rows = this.latencies.get(lane)!;
    rows.push(value);
    if (rows.length > 512) rows.shift();
  }

  private validatePolicy(): void {
    for (const lane of LANES) {
      const value = this.policy[lane];
      const numbers = [value.maxMessages, value.maxBytes, value.highWaterMark, value.lowWaterMark, value.weight];
      if (numbers.some(number => !Number.isSafeInteger(number) || number <= 0)) {
        throw new Error('webrtc_queue_policy_invalid');
      }
      if (value.lowWaterMark > value.highWaterMark) throw new Error('webrtc_queue_policy_invalid');
    }
  }
}

export function laneForTrafficClass(trafficClass: SemanticTrafficClass): WebrtcQueueLane {
  if (trafficClass === 'control') return 'urgent';
  if (trafficClass === 'transcript') return 'interactive';
  if (trafficClass === 'audio_recovery' || trafficClass === 'visual_semantic' || trafficClass === 'diagnostic') return 'live';
  return 'bulk';
}
