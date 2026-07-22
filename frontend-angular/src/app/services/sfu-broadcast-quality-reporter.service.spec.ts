import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { SfuBroadcastQualityApiService } from './sfu-broadcast-quality-api.service';
import {
  SFU_QUALITY_REPORTER_ENVIRONMENT,
  SfuBroadcastQualityReporterService,
  type SfuQualityReporterEnvironment,
} from './sfu-broadcast-quality-reporter.service';
import { SfuBroadcastQualitySamplerService } from './sfu-broadcast-quality-sampler.service';
import type { SfuRemoteVideoHandle } from './sfu-room-session.ports';

describe('SfuBroadcastQualityReporterService', () => {
  it('coalesces bounded samples, retries the same sequence and cancels cleanup resources', async () => {
    let now = Date.parse('2026-07-22T10:00:00Z');
    const timers = new Map<number, () => void>();
    let timerId = 0;
    const environment: SfuQualityReporterEnvironment = {
      nowMs: () => now,
      randomBytes: length => new Uint8Array(length).fill(7),
      online: () => true,
      hidden: () => false,
      setTimer: callback => { const id = ++timerId; timers.set(id, callback); return id as any; },
      clearTimer: timer => { timers.delete(timer as any); },
    };
    const responses = new Subject<any>();
    const api = { submit: vi.fn(() => responses.asObservable()) };
    const sampler = { sample: vi.fn(async () => ({
      sample_sequence: 1, observed_at: '2026-07-22T10:00:01Z', window_ms: 1000,
      metrics: { rtt_ms: 50 },
    })), reset: vi.fn() };
    TestBed.configureTestingModule({ providers: [
      SfuBroadcastQualityReporterService,
      { provide: SfuBroadcastQualityApiService, useValue: api },
      { provide: SfuBroadcastQualitySamplerService, useValue: sampler },
      { provide: SFU_QUALITY_REPORTER_ENVIRONMENT, useValue: environment },
    ] });
    const service = TestBed.inject(SfuBroadcastQualityReporterService);
    service.start(binding(), {
      capability: 'available',
      read: vi.fn(),
      authorizesQualityBinding: (candidate, scope) => (
        candidate === handle
        && scope.publicationRef === 'camera-a'
        && scope.routeEpoch === 5
        && scope.membershipEpoch === 3
      ),
    }, handle);

    for (let index = 0; index < 5; index += 1) {
      now += 1000;
      const callback = [...timers.values()][0]; timers.clear(); callback();
      await Promise.resolve(); await Promise.resolve();
    }
    expect(api.submit).toHaveBeenCalledOnce();
    const firstReport = api.submit.mock.calls[0][1];
    expect(firstReport.sequence).toBe(1);
    expect(firstReport.samples.length).toBeLessThanOrEqual(16);
    expect(new TextEncoder().encode(JSON.stringify(firstReport)).byteLength).toBeLessThanOrEqual(8192);
    expect(JSON.stringify(firstReport)).not.toMatch(/candidate|device|track.id|sdp|hardware/i);

    responses.error(new Error('offline'));
    await Promise.resolve(); await Promise.resolve();
    now += 250;
    const retry = [...timers.values()][0]; timers.clear(); retry();
    await Promise.resolve(); await Promise.resolve();
    expect(api.submit.mock.calls[1][1]).toBe(firstReport);

    service.stop();
    expect(timers.size).toBe(0);
    expect(sampler.reset).toHaveBeenCalledWith(handle.handleId);
  });
});

const handle: SfuRemoteVideoHandle = {
  handleId: 'sfu-video-1', source: 'camera',
};

function binding() {
  return {
    hubUrl: 'https://hub.test', sessionId: 'session-a', membershipEpoch: 3,
    subscriptionRef: 'subscription-a', tenantRef: 'tenant-a', roomRef: 'room-a',
    subscriberRef: 'bob', publicationRef: 'camera-a', routeEpoch: 5,
    requestedLayer: null, allowedLayer: null, effectiveLayer: null,
  };
}
