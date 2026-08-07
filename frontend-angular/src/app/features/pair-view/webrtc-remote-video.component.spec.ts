import { TestBed } from '@angular/core/testing';

import { WebrtcRemoteVideoComponent } from './webrtc-remote-video.component';
import { WebrtcRemoteVideoRenderService } from './webrtc-remote-video-render.service';

describe('WebrtcRemoteVideoComponent', () => {
  afterEach(() => vi.restoreAllMocks());

  it('attaches an ordinary remote publication, reports autoplay blocking and only releases the view', async () => {
    const release = vi.fn();
    const renderer = { attach: vi.fn(() => release) };
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValue(new Error('autoplay_blocked'));
    await TestBed.configureTestingModule({ imports: [WebrtcRemoteVideoComponent] })
      .overrideComponent(WebrtcRemoteVideoComponent, {
        set: { providers: [{ provide: WebrtcRemoteVideoRenderService, useValue: renderer }] },
      })
      .compileComponents();

    const fixture = TestBed.createComponent(WebrtcRemoteVideoComponent);
    fixture.componentRef.setInput('publication', {
      publicationId: 'remote-camera-a', source: 'remote_video', status: 'active', local: false,
      trackId: 'track-a', captureLabel: 'Remote-Kamera', reasonCode: null,
    });
    fixture.detectChanges();
    await vi.waitFor(() => expect(fixture.componentInstance.status).toBe('blocked'));
    fixture.detectChanges();

    expect(renderer.attach).toHaveBeenCalledWith(
      'remote-camera-a', expect.any(HTMLVideoElement),
    );
    expect(fixture.nativeElement.textContent).toContain('Browser wartet auf Wiedergabefreigabe');
    expect(fixture.nativeElement.querySelector('button')?.textContent).toContain('Video wiedergeben');

    fixture.destroy();
    expect(release).toHaveBeenCalledTimes(1);
  });
});
