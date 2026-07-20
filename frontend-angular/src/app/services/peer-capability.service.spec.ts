import { TestBed } from '@angular/core/testing';

import {
  PeerCapabilityService,
  parseCapabilityAdvertisement,
} from './peer-capability.service';

describe('PeerCapabilityService', () => {
  let service: PeerCapabilityService;
  const signer = { sign: vi.fn(async () => ({ algorithm: 'hmac-sha256' as const, key_id: 'test', value: 'a'.repeat(64) })) };

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [PeerCapabilityService] });
    service = TestBed.inject(PeerCapabilityService);
  });

  afterEach(() => {
    service.ngOnDestroy();
    vi.restoreAllMocks();
  });

  const options = () => ({
    consent: { granted: true, version: 1 }, sessionId: 'session-a', epoch: 1, senderId: 'peer-a',
    limits: { cpu: 'low' as const, memory: 'low' as const, maxDelayMs: 6_999, maxArtifactBytes: 10_999 },
    selfClaim: { cpu: 'high' as const, memory: 'high' as const },
    algorithms: ['heuristic-visual-v1'], roles: ['executor' as const], taskTypes: ['visual_extract' as const],
    signer, clock: { now: () => 1_000_000 },
  });

  it('starts only after explicit opt-in and exports short-lived coarse buckets', async () => {
    await expect(service.measureAndAdvertise({ ...options(), consent: { granted: false, version: 0 } }))
      .rejects.toThrow('compute_consent_required');
    const result = await service.measureAndAdvertise(options());
    expect(result.resource_profile.cpu).toBe('low');
    expect(result.resource_profile.memory).toBe('unknown');
    expect(result.max_delay_ms).toBe(6_000);
    expect(result.max_artifact_bytes).toBe(10_240);
    expect(result.expires_at_ms).toBe(1_060_000);
    expect(result.measurements_expires_at_ms).toBe(result.expires_at_ms);
    expect(JSON.stringify(result)).not.toMatch(/hardwareConcurrency|deviceMemory|benchmark|level/);
  });

  it('uses unknown when optional GPU, Battery, Memory and Network APIs are absent', async () => {
    const result = await service.measureAndAdvertise(options());
    expect(result.resource_profile.memory).toBe('unknown');
    expect(result.resource_profile.battery).toBe('unknown');
    expect(result.resource_profile.network).toBe('unknown');
    expect(['unknown', 'integrated']).toContain(result.resource_profile.gpu);
  });

  it('runtime outcomes and user limits cap self claims', async () => {
    service.recordRuntimeOutcome({ cpu: 'low', successful: false });
    const result = await service.measureAndAdvertise({
      ...options(), limits: { ...options().limits, cpu: 'high' }, selfClaim: { cpu: 'high', memory: 'high' },
    });
    expect(result.resource_profile.cpu).toBe('low');
  });

  it('rejects an advertisement exactly when the injected clock reaches its TTL', async () => {
    let nowMs = 1_000_000;
    const result = await service.measureAndAdvertise({
      ...options(),
      clock: { now: () => nowMs },
    });

    nowMs = result.expires_at_ms - 1;
    expect(parseCapabilityAdvertisement(result, nowMs)).toEqual(result);

    nowMs = result.expires_at_ms;
    expect(() => parseCapabilityAdvertisement(result, nowMs)).toThrow('capability_expired');
  });

  it('cancels an in-flight measurement on consent revoke or session end', async () => {
    let resolveBattery!: (value: { charging: boolean; level: number }) => void;
    Object.defineProperty(navigator, 'getBattery', {
      configurable: true,
      value: () => new Promise(resolve => { resolveBattery = resolve; }),
    });
    const pending = service.measureAndAdvertise(options());
    service.revokeConsent();
    resolveBattery({ charging: true, level: 1 });
    await expect(pending).rejects.toThrow('measurement_cancelled');

    const second = service.measureAndAdvertise(options());
    service.endSession('session-a');
    resolveBattery({ charging: true, level: 1 });
    await expect(second).rejects.toThrow('measurement_cancelled');
    delete (navigator as Navigator & { getBattery?: unknown }).getBattery;
  });

  it('stops immediately when the document becomes hidden', () => {
    const stop = vi.spyOn(service, 'stop');
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(stop).toHaveBeenCalledWith('visibility_lost');
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  });
});
