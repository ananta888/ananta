import { TestBed } from '@angular/core/testing';

import { WebrtcMediaPublicationService } from '../../services/webrtc-media-publication.service';
import { WebrtcRemoteVideoRenderService } from './webrtc-remote-video-render.service';

describe('WebrtcRemoteVideoRenderService', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('detaches its MediaStream view without stopping the borrowed receiver track', () => {
    const track = { stop: vi.fn() } as unknown as MediaStreamTrack;
    class TestMediaStream {
      constructor(readonly tracks: readonly MediaStreamTrack[]) {}
    }
    vi.stubGlobal('MediaStream', TestMediaStream);
    TestBed.configureTestingModule({ providers: [
      WebrtcRemoteVideoRenderService,
      { provide: WebrtcMediaPublicationService, useValue: { remoteVideoTrack: () => track } },
    ] });
    const renderer = TestBed.inject(WebrtcRemoteVideoRenderService);
    const element = document.createElement('video');

    const release = renderer.attach('remote-video-a', element);
    expect((element.srcObject as unknown as TestMediaStream).tracks).toEqual([track]);
    release();

    expect(element.srcObject).toBeNull();
    expect(track.stop).not.toHaveBeenCalled();
  });
});
