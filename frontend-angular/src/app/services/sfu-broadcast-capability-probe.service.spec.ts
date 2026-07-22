import { TestBed } from '@angular/core/testing';

import {
  SFU_BROWSER_CAPABILITY_PROBE_ENVIRONMENT,
  SfuBroadcastCapabilityProbeService,
  SfuBrowserCapabilityProbeEnvironment,
} from './sfu-broadcast-capability-probe.service';

describe('SfuBroadcastCapabilityProbeService', () => {
  let nowMs: number;
  let randomSeed: number;
  let environment: SfuBrowserCapabilityProbeEnvironment;

  beforeEach(() => {
    nowMs = Date.parse('2026-07-22T10:00:00Z');
    randomSeed = 1;
    environment = {
      nowMs: () => nowMs,
      randomBytes: length => new Uint8Array(length).fill(randomSeed++),
      senderCodecs: kind => kind === 'audio'
        ? [{ mimeType: 'audio/opus' }]
        : [{ mimeType: 'video/VP8' }, { mimeType: 'video/VP9', scalabilityModes: ['L2T2'] }],
      receiverCodecs: kind => kind === 'audio'
        ? [{ mimeType: 'audio/opus' }]
        : [{ mimeType: 'video/VP8' }, { mimeType: 'video/VP9', scalabilityModes: ['L2T2'] }],
      encodedTransformAvailable: () => true,
      simulcastApiAvailable: () => true,
    };
    TestBed.configureTestingModule({
      providers: [
        SfuBroadcastCapabilityProbeService,
        { provide: SFU_BROWSER_CAPABILITY_PROBE_ENVIRONMENT, useValue: environment },
      ],
    });
  });

  const scope = () => ({
    tenantRef: 'tenant-capability', roomRef: 'sfu-0123456789abcdef0123456789abcdef',
    admissionEpoch: 3, membershipEpoch: 7,
  });

  it('uses permission-free static queries and exports only closed coarse buckets', () => {
    const result = TestBed.inject(SfuBroadcastCapabilityProbeService).probe(scope());
    expect(result.status).toBe('fully_supported');
    expect(result.parentFallbackRequired).toBe(false);
    expect(result.observation.capability_buckets).toEqual([
      expect.objectContaining({ codec_bucket: 'audio_opus', layering_bucket: 'unsupported' }),
      expect.objectContaining({ codec_bucket: 'video_vp8', layering_bucket: 'simulcast' }),
      expect.objectContaining({ codec_bucket: 'video_vp9', layering_bucket: 'svc' }),
    ]);
    expect(JSON.stringify(result.observation)).not.toMatch(
      /device|hardware|fingerprint|user.agent|ip.address|sdp|media.content|client.id/i,
    );
    expect(new TextEncoder().encode(JSON.stringify(result.observation)).byteLength).toBeLessThanOrEqual(2048);
  });

  it('fails visibly to the parent fallback when static browser APIs are missing', () => {
    environment.senderCodecs = () => null;
    const result = TestBed.inject(SfuBroadcastCapabilityProbeService).probe(scope());
    expect(result.status).toBe('unknown');
    expect(result.parentFallbackRequired).toBe(true);
    expect(result.observation.capability_buckets).toEqual([{
      codec_bucket: 'unknown', layering_bucket: 'unknown', encoded_transform_bucket: 'unknown',
      decode_bucket: 'unknown', evidence_bucket: 'not_observed',
    }]);
  });

  it('rotates room pseudonyms, sequences reports, and forgets revoked scopes', () => {
    const service = TestBed.inject(SfuBroadcastCapabilityProbeService);
    const first = service.probe(scope()).observation;
    const second = service.probe(scope()).observation;
    expect(second.browser_instance_pseudonym).toBe(first.browser_instance_pseudonym);
    expect(second.sequence).toBe(2);
    nowMs += 900_000;
    const rotated = service.probe(scope()).observation;
    expect(rotated.browser_instance_pseudonym).not.toBe(first.browser_instance_pseudonym);
    expect(rotated.sequence).toBe(1);
    service.clearScope(scope());
    expect(service.probe(scope()).observation.browser_instance_pseudonym)
      .not.toBe(rotated.browser_instance_pseudonym);
  });

  it('bounds capability combinations even when an implementation repeats codecs', () => {
    const repeated = Array.from({ length: 40 }, (_, index) => ({
      mimeType: ['audio/opus', 'video/VP8', 'video/H264', 'video/VP9', 'video/AV1'][index % 5],
      scalabilityModes: ['L3T3'],
    }));
    environment.senderCodecs = () => repeated;
    environment.receiverCodecs = () => repeated;
    const observation = TestBed.inject(SfuBroadcastCapabilityProbeService).probe(scope()).observation;
    expect(observation.capability_buckets.length).toBeLessThanOrEqual(8);
    expect(new Set(observation.capability_buckets.map(row => JSON.stringify(row))).size)
      .toBe(observation.capability_buckets.length);
  });
});
