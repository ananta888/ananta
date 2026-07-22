import { VideoQuality, type RemoteTrackPublication } from 'livekit-client';

export type SfuReceiverVideoQuality = 'low' | 'medium' | 'high';

/** The only adapter allowed to translate Hub layer projections into LiveKit calls. */
export class LivekitSfuReceiverLayerAdapter {
  apply(publication: RemoteTrackPublication, quality: SfuReceiverVideoQuality): void {
    const mapped = quality === 'low'
      ? VideoQuality.LOW
      : quality === 'medium' ? VideoQuality.MEDIUM : VideoQuality.HIGH;
    publication.setVideoQuality(mapped);
  }
}
