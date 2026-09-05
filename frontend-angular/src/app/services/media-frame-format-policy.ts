export type MediaFrameFormat = 'legacy_anme_v1' | 'sframe_rfc9605';

export interface MediaFrameFormatDecision {
  readonly acceptedFormat: MediaFrameFormat;
  readonly nativeSframeRequired: boolean;
  readonly legacyCompatibilityAllowed: boolean;
}

/** Explicit compatibility boundary; ANME is never represented as SFrame. */
export class MediaFrameFormatPolicy {
  decide(requested: string, nativeSframeAvailable: boolean): MediaFrameFormatDecision {
    if (requested === 'sframe_rfc9605') {
      if (!nativeSframeAvailable) throw new Error('media_sframe_adapter_unavailable');
      return Object.freeze({
        acceptedFormat: requested,
        nativeSframeRequired: true,
        legacyCompatibilityAllowed: false,
      });
    }
    if (requested === 'legacy_anme_v1') {
      return Object.freeze({
        acceptedFormat: requested,
        nativeSframeRequired: false,
        legacyCompatibilityAllowed: true,
      });
    }
    throw new Error('media_frame_format_unsupported');
  }

  assertNoDowngrade(negotiated: MediaFrameFormat, received: MediaFrameFormat): void {
    if (negotiated !== received) throw new Error('media_frame_format_downgrade');
  }
}
