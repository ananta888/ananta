import { MediaFrameFormatPolicy } from './media-frame-format-policy';

describe('MediaFrameFormatPolicy', () => {
  const policy = new MediaFrameFormatPolicy();

  it('labels ANME only as an explicit legacy compatibility format', () => {
    expect(policy.decide('legacy_anme_v1', false)).toEqual({
      acceptedFormat: 'legacy_anme_v1',
      nativeSframeRequired: false,
      legacyCompatibilityAllowed: true,
    });
  });

  it('requires a real native adapter for RFC 9605 SFrame', () => {
    expect(() => policy.decide('sframe_rfc9605', false)).toThrow('media_sframe_adapter_unavailable');
    expect(policy.decide('sframe_rfc9605', true).acceptedFormat).toBe('sframe_rfc9605');
  });

  it('rejects unknown formats and negotiated downgrade confusion', () => {
    expect(() => policy.decide('ANME-is-SFrame', true)).toThrow('media_frame_format_unsupported');
    expect(() => policy.assertNoDowngrade('sframe_rfc9605', 'legacy_anme_v1'))
      .toThrow('media_frame_format_downgrade');
  });
});
