import { Injectable } from '@angular/core';

import { PUBLIC_PAIR_MEDIA_CAPABILITIES_V1 } from './public-pair-media-security-contract';

export interface PublicPairMediaMembershipAdvertisementV1 {
  readonly public_media_e2ee_version: 1;
  readonly public_media_capabilities: typeof PUBLIC_PAIR_MEDIA_CAPABILITIES_V1;
}

/** Runtime surface required by the standards-only first Public Pair slice. */
interface EncodedTransformRuntime {
  readonly RTCRtpScriptTransform?: unknown;
  readonly RTCRtpSender?: {
    readonly prototype?: object;
    getCapabilities?(kind: string): RTCRtpCapabilities | null;
  };
  readonly RTCRtpReceiver?: { readonly prototype?: object };
  readonly RTCRtpTransceiver?: { readonly prototype?: object };
  readonly Worker?: unknown;
  readonly TransformStream?: unknown;
  readonly crypto?: {
    readonly getRandomValues?: unknown;
    readonly subtle?: Partial<SubtleCrypto>;
  };
}

@Injectable({ providedIn: 'root' })
export class PublicPairMediaRuntimeCapabilityService {
  supported(): boolean {
    return supportsPublicPairMediaRuntime(globalThis as EncodedTransformRuntime);
  }

  membershipAdvertisement(): PublicPairMediaMembershipAdvertisementV1 | null {
    if (!this.supported()) return null;
    return Object.freeze({
      public_media_e2ee_version: 1,
      public_media_capabilities: PUBLIC_PAIR_MEDIA_CAPABILITIES_V1,
    });
  }
}

export function supportsPublicPairMediaRuntime(runtime: EncodedTransformRuntime): boolean {
  const subtle = runtime.crypto?.subtle;
  return typeof runtime.RTCRtpScriptTransform === 'function'
    && typeof runtime.Worker === 'function'
    && typeof runtime.TransformStream === 'function'
    && typeof runtime.crypto?.getRandomValues === 'function'
    && typeof subtle?.digest === 'function'
    && typeof subtle?.importKey === 'function'
    && typeof subtle?.deriveKey === 'function'
    && typeof subtle?.encrypt === 'function'
    && typeof subtle?.decrypt === 'function'
    && hasTransformProperty(runtime.RTCRtpSender?.prototype)
    && hasTransformProperty(runtime.RTCRtpReceiver?.prototype)
    && typeof (runtime.RTCRtpTransceiver?.prototype as { setCodecPreferences?: unknown } | undefined)
      ?.setCodecPreferences === 'function'
    && hasCodec(runtime.RTCRtpSender, 'audio', 'audio/opus')
    && hasCodec(runtime.RTCRtpSender, 'video', 'video/vp8');
}

function hasTransformProperty(value: object | undefined): boolean {
  return Boolean(value && 'transform' in value);
}

function hasCodec(
  sender: EncodedTransformRuntime['RTCRtpSender'],
  kind: 'audio' | 'video',
  mimeType: string,
): boolean {
  try {
    return sender?.getCapabilities?.(kind)?.codecs
      .some(codec => codec.mimeType.toLowerCase() === mimeType) === true;
  } catch {
    return false;
  }
}
