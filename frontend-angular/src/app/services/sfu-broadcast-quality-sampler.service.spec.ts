import { describe, expect, it, vi } from 'vitest';

import { SfuBroadcastQualitySamplerService } from './sfu-broadcast-quality-sampler.service';
import type { SfuRemoteVideoHandle, SfuStatsPort } from './sfu-room-session.ports';

describe('SfuBroadcastQualitySamplerService', () => {
  it('projects only bounded counters and never exports raw RTC identifiers', async () => {
    const snapshots = [
      { observedAtMs: 1000, bytesReceived: 1000, packetsReceived: 100, packetsLost: 1,
        framesDecoded: 10, totalDecodeTimeSeconds: .1, totalFreezeDurationSeconds: 0,
        jitterSeconds: .01, roundTripTimeSeconds: .05 },
      { observedAtMs: 2000, bytesReceived: 3000, packetsReceived: 200, packetsLost: 3,
        framesDecoded: 30, totalDecodeTimeSeconds: .3, totalFreezeDurationSeconds: .2,
        jitterSeconds: .02, roundTripTimeSeconds: .08 },
    ];
    const port: SfuStatsPort = {
      capability: 'available', read: vi.fn(async () => snapshots.shift() ?? null),
    };
    const service = new SfuBroadcastQualitySamplerService();
    await service.sample(port, handle);
    const sample = await service.sample(port, handle);
    expect(sample).toMatchObject({
      sample_sequence: 2, window_ms: 1000,
      metrics: {
        rtt_ms: 80, jitter_ms: 20, packet_loss_basis_points: 196,
        receive_bitrate_bps: 16000, decode_time_ms_per_frame: 10, freeze_duration_ms: 200,
      },
    });
    expect(JSON.stringify(sample)).not.toMatch(/candidate|address|device|label|track|sdp|codec/i);
  });

  it('fails closed when stats are unsupported or missing', async () => {
    const service = new SfuBroadcastQualitySamplerService();
    await expect(service.sample({ capability: 'unsupported' }, handle)).resolves.toBeNull();
    await expect(service.sample({ capability: 'available' }, handle)).resolves.toBeNull();
  });
});

const handle: SfuRemoteVideoHandle = {
  handleId: 'sfu-video-1', source: 'camera',
};
