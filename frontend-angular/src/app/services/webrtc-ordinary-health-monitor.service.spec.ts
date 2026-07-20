import { TestBed } from '@angular/core/testing';

import { WebrtcMediaHealthService } from './webrtc-media-health.service';
import {
  ORDINARY_FALLBACK_HEALTH_POLICY,
  WebrtcOrdinaryHealthMonitorService,
} from './webrtc-ordinary-health-monitor.service';
import { WebrtcSessionService } from './webrtc-session.service';

function stats(index: number): readonly RTCStats[] {
  return [
    {
      id: 'inbound',
      type: 'inbound-rtp',
      timestamp: index * 250,
      trackIdentifier: 'audio-track',
      bytesReceived: 1_000 + index * 1_000,
      packetsReceived: 100 + index * 100,
      packetsLost: 1 + index,
      jitter: 0.01,
      framesDecoded: 10 + index * 10,
      freezeCount: 0,
      concealmentEvents: 0,
      silentConcealedSamples: 0,
    } as RTCStats,
    {
      id: 'pair',
      type: 'candidate-pair',
      timestamp: index * 250,
      state: 'succeeded',
      currentRoundTripTime: 0.05,
    } as RTCStats,
  ];
}

describe('WebrtcOrdinaryHealthMonitorService', () => {
  afterEach(() => vi.useRealTimers());

  it('collects one warmup and three healthy windows before returning ready', async () => {
    vi.useFakeTimers();
    let index = 0;
    const peer = {
      ordinaryMediaStats: vi.fn(async () => ({
        connection: 'connected' as RTCPeerConnectionState,
        stats: stats(index++),
      })),
    };
    TestBed.configureTestingModule({ providers: [
      WebrtcOrdinaryHealthMonitorService,
      WebrtcMediaHealthService,
      { provide: WebrtcSessionService, useValue: peer },
    ] });
    const monitor = TestBed.inject(WebrtcOrdinaryHealthMonitorService);
    const readiness = monitor.requireReady('session-a', 'peer-b');
    await vi.runAllTimersAsync();
    await expect(readiness).resolves.toBeUndefined();
    expect(peer.ordinaryMediaStats).toHaveBeenCalledTimes(
      ORDINARY_FALLBACK_HEALTH_POLICY.requiredHealthyWindows + 1,
    );
  });

  it('fails closed after a bounded number of disconnected samples', async () => {
    vi.useFakeTimers();
    const peer = {
      ordinaryMediaStats: vi.fn(async () => ({
        connection: 'disconnected' as RTCPeerConnectionState,
        stats: stats(0),
      })),
    };
    TestBed.configureTestingModule({ providers: [
      WebrtcOrdinaryHealthMonitorService,
      WebrtcMediaHealthService,
      { provide: WebrtcSessionService, useValue: peer },
    ] });
    const monitor = TestBed.inject(WebrtcOrdinaryHealthMonitorService);
    const readiness = monitor.requireReady('session-a', 'peer-b');
    const rejected = expect(readiness).rejects.toThrow('ordinary_fallback_not_healthy');
    await vi.runAllTimersAsync();
    await rejected;
    expect(peer.ordinaryMediaStats).toHaveBeenCalledTimes(
      ORDINARY_FALLBACK_HEALTH_POLICY.maximumSamples,
    );
  });
});
