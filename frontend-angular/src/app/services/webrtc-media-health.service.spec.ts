import { WebrtcMediaHealthService } from './webrtc-media-health.service';

function stats(patch: Record<string, unknown> = {}): RTCStats[] {
  return [
    { id: 'in', type: 'inbound-rtp', timestamp: 0, trackIdentifier: 'audio', bytesReceived: 1000,
      packetsReceived: 100, packetsLost: 1, jitter: 0.01, framesDecoded: 10,
      freezeCount: 0, concealmentEvents: 0, silentConcealedSamples: 0, ...patch } as any,
    { id: 'pair', type: 'candidate-pair', timestamp: 0, state: 'succeeded', currentRoundTripTime: 0.05,
      localCandidateId: 'private-ip-must-not-export', remoteCandidateId: 'remote-ip-must-not-export' } as any,
  ];
}

describe('WebrtcMediaHealthService', () => {
  it('uses fixed units, bounded history and requires three healthy ordinary windows before semantic activation', () => {
    const service = new WebrtcMediaHealthService();
    service.ingest('session', 'peer', 'connected', stats(), 0, 1000);
    for (let index = 1; index <= 3; index += 1) {
      const window = service.ingest('session', 'peer', 'connected', stats({
        bytesReceived: 1000 + index * 1000, packetsReceived: 100 + index * 100,
        packetsLost: 1 + index, framesDecoded: 10 + index * 10,
      }), index * 1000, (index + 1) * 1000);
      expect(window).toMatchObject({ status: 'healthy', rtt_ms: 50, jitter_ms: 10, bitrate_bps: 8000 });
    }
    expect(service.ordinaryFallbackReady('session', 'peer')).toBe(true);
    expect(service.exportForHub('session', 'peer', 10)).toHaveLength(4);
    expect(JSON.stringify(service.exportForHub('session', 'peer'))).not.toMatch(/Candidate|device|private-ip|remote-ip|mediaContent/i);
  });

  it.each([
    ['missing browser fields', {}, 'unknown'],
    ['NaN fields', { jitter: Number.NaN, currentRoundTripTime: Number.NaN }, 'degraded'],
    ['counter reset', { bytesReceived: 1, packetsReceived: 1, packetsLost: 0 }, 'unknown'],
    ['track change', { trackIdentifier: 'new-track', bytesReceived: 2000, packetsReceived: 200 }, 'unknown'],
  ])('handles %s without fabricated healthy evidence', (_name, patch, status) => {
    const service = new WebrtcMediaHealthService();
    service.ingest('session', 'peer', 'connected', stats(), 0, 1000);
    const nextStats = Object.keys(patch).length === 0
      ? [{ id: 'in', type: 'inbound-rtp', timestamp: 0 } as any]
      : stats(patch);
    expect(service.ingest('session', 'peer', 'connected', nextStats, 1000, 2000).status).toBe(status);
    expect(service.ordinaryFallbackReady('session', 'peer')).toBe(false);
  });

  it('marks abrupt disconnect and resets readiness on session/track lifecycle changes', () => {
    const service = new WebrtcMediaHealthService();
    service.ingest('session', 'peer', 'connected', stats(), 0, 1000);
    const disconnected = service.ingest('session', 'peer', 'disconnected', stats({
      bytesReceived: 2000, packetsReceived: 200, framesDecoded: 20,
    }), 1000, 2000);
    expect(disconnected).toMatchObject({ status: 'disconnected', reason_code: 'ordinary_connection_lost' });
    expect(service.ordinaryFallbackReady('session', 'peer')).toBe(false);
  });
});
