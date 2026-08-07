import { Injectable, inject } from '@angular/core';

import { WebrtcMediaPublicationService } from '../../services/webrtc-media-publication.service';

export type RemoteVideoRelease = () => void;

/**
 * Browser adapter for ordinary remote video. It only borrows the receiver
 * track; releasing a view detaches the element and never stops that track.
 */
@Injectable()
export class WebrtcRemoteVideoRenderService {
  private readonly publications = inject(WebrtcMediaPublicationService);

  attach(publicationId: string, element: HTMLVideoElement): RemoteVideoRelease {
    const track = this.publications.remoteVideoTrack(publicationId);
    if (!track) throw new Error('remote_video_track_unavailable');
    if (typeof globalThis.MediaStream !== 'function') throw new Error('remote_video_stream_unavailable');
    const stream = new MediaStream([track]);
    element.srcObject = stream;
    return () => {
      if (element.srcObject === stream) element.srcObject = null;
    };
  }
}
