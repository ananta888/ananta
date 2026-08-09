import {
  PUBLIC_PAIR_MEDIA_CAPABILITIES_V1,
} from './public-pair-media-security-contract';
import {
  PublicPairMediaRuntimeCapabilityService,
  supportsPublicPairMediaRuntime,
} from './public-pair-media-runtime-capability.service';

describe('PublicPairMediaRuntimeCapabilityService', () => {
  const supportedRuntime = () => ({
    RTCRtpScriptTransform: class {},
    Worker: class {},
    TransformStream: class {},
    crypto: {
      getRandomValues: () => undefined,
      subtle: {
        digest: () => undefined, importKey: () => undefined, deriveKey: () => undefined,
        encrypt: () => undefined, decrypt: () => undefined,
      } as unknown as SubtleCrypto,
    },
    RTCRtpSender: {
      prototype: { transform: null },
      getCapabilities: (kind: string) => ({
        codecs: [{ mimeType: kind === 'audio' ? 'audio/opus' : 'video/VP8' }],
      }),
    },
    RTCRtpReceiver: { prototype: { transform: null } },
    RTCRtpTransceiver: { prototype: { setCodecPreferences: () => undefined } },
  });

  it('advertises the exact immutable v1 profile only with the complete standard surface', () => {
    const service = new PublicPairMediaRuntimeCapabilityService();
    vi.spyOn(service, 'supported').mockReturnValue(true);

    expect(service.membershipAdvertisement()).toEqual({
      public_media_e2ee_version: 1,
      public_media_capabilities: PUBLIC_PAIR_MEDIA_CAPABILITIES_V1,
    });
    expect(Object.isFrozen(service.membershipAdvertisement())).toBe(true);
  });

  it.each([
    'RTCRtpScriptTransform', 'Worker', 'TransformStream', 'crypto',
    'RTCRtpSender', 'RTCRtpReceiver', 'RTCRtpTransceiver',
  ] as const)(
    'omits the extension when %s is unavailable',
    missing => {
      const runtime = supportedRuntime() as Record<string, unknown>;
      delete runtime[missing];
      expect(supportsPublicPairMediaRuntime(runtime)).toBe(false);
    },
  );

  it('omits the extension when exact Opus/VP8 codec preferences cannot be enforced', () => {
    const runtime = supportedRuntime();
    runtime.RTCRtpSender.getCapabilities = kind => ({
      codecs: [{ mimeType: kind === 'audio' ? 'audio/PCMU' : 'video/H264' }],
    });
    expect(supportsPublicPairMediaRuntime(runtime)).toBe(false);
  });
});
