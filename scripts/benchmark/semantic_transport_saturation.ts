import {
  DataChannelSendPort,
  WebrtcPrioritySendQueue,
  WebrtcQueuePolicy,
} from '../../frontend-angular/src/app/services/webrtc-priority-send-queue';

const SAMPLE_COUNT = 128;
const POLICY: WebrtcQueuePolicy = Object.freeze({
  urgent: Object.freeze({
    maxMessages: 256, maxBytes: 2_097_152, highWaterMark: 1_048_576, lowWaterMark: 1, weight: 8,
  }),
  interactive: Object.freeze({
    maxMessages: 256, maxBytes: 2_097_152, highWaterMark: 1_048_576, lowWaterMark: 1, weight: 6,
  }),
  live: Object.freeze({
    maxMessages: 64, maxBytes: 1_048_576, highWaterMark: 512, lowWaterMark: 1, weight: 2,
  }),
  bulk: Object.freeze({
    maxMessages: 64, maxBytes: 1_048_576, highWaterMark: 1, lowWaterMark: 1, weight: 1,
  }),
});

class SaturatedChannel implements DataChannelSendPort {
  bufferedAmount = 2;
  bufferedAmountLowThreshold = 0;
  readyState = 'closed';
  controlSent = 0;
  transcriptSent = 0;
  bulkSent = 0;

  send(value: string): void {
    if (value.startsWith('control-')) this.controlSent += 1;
    else if (value.startsWith('revision-')) this.transcriptSent += 1;
    else this.bulkSent += 1;
  }
}

async function main(): Promise<void> {
  const queue = new WebrtcPrioritySendQueue(POLICY, () => Date.now());
  const channel = new SaturatedChannel();
  queue.bind(channel);
  const expiresAtMs = Date.now() + 60_000;
  for (let index = 0; index < POLICY.bulk.maxMessages; index += 1) {
    if (!queue.enqueue('evidence_bulk', `bulk-primer-${index}`, expiresAtMs)) {
      throw new Error('transport_saturation_primer_rejected');
    }
  }
  const initial = queue.snapshot();
  if (initial.messages.bulk !== POLICY.bulk.maxMessages || initial.bytes.bulk <= 0) {
    throw new Error('transport_saturation_not_established');
  }

  for (let index = 0; index < SAMPLE_COUNT; index += 1) {
    channel.readyState = 'closed';
    queue.enqueue('evidence_bulk', `bulk-pressure-${index}`, expiresAtMs);
    if (!queue.enqueue('control', `control-${index}`, expiresAtMs)) {
      throw new Error('transport_control_enqueue_failed');
    }
    if (!queue.enqueue('transcript', `revision-${index}`, expiresAtMs)) {
      throw new Error('transport_revision_enqueue_failed');
    }
    await new Promise(resolve => setTimeout(resolve, 2));
    channel.readyState = 'open';
    channel.bufferedAmount = 2;
    queue.flush();
  }

  const final = queue.snapshot();
  const report = {
    schema: 'ananta.semantic-transport-saturation.v1',
    sample_count: SAMPLE_COUNT,
    bulk_saturated_samples: SAMPLE_COUNT,
    initial_bulk_messages: initial.messages.bulk,
    final_bulk_messages: final.messages.bulk,
    control_sent: channel.controlSent,
    revision_sent: channel.transcriptSent,
    bulk_sent: channel.bulkSent,
    control_p95_ms: queue.p95Latency('urgent'),
    revision_p95_ms: queue.p95Latency('interactive'),
    timer_count: final.timers,
    bulk_eviction_count: final.drops['bulk_evicted'] ?? 0,
  };
  process.stdout.write(`${JSON.stringify(report)}\n`);
}

await main();
