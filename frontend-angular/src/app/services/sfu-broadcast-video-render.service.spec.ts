import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LivekitSfuTransportService } from './livekit-sfu-transport.service';
import { SfuBroadcastVideoRenderService } from './sfu-broadcast-video-render.service';
import type { SfuRemoteVideoHandle } from './sfu-room-session.ports';

describe('SfuBroadcastVideoRenderService', () => {
  const remoteVideo$ = new Subject<SfuRemoteVideoHandle>();
  const remoteVideoUnavailable$ = new Subject<Readonly<{ handleId: string; reason: 'unsubscribed' | 'ended' }>>();
  const remoteVideoState$ = new Subject<Readonly<{ handleId: string; state: 'active' | 'muted' }>>();
  const state$ = new BehaviorSubject({ status: 'connected' });
  const release = vi.fn();
  const transport = {
    remoteVideo$, remoteVideoUnavailable$, remoteVideoState$, state$,
    attachRemoteVideo: vi.fn(() => release),
  };
  let service: SfuBroadcastVideoRenderService;

  beforeEach(() => {
    release.mockClear();
    transport.attachRemoteVideo.mockClear();
    state$.next({ status: 'connected' });
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      SfuBroadcastVideoRenderService,
      { provide: LivekitSfuTransportService, useValue: transport },
    ] });
    service = TestBed.inject(SfuBroadcastVideoRenderService);
  });

  it('publishes only opaque UI-safe views and releases attachment on unsubscribe', () => {
    const handle = Object.freeze({ handleId: 'sfu-video-1', source: 'camera' as const });
    remoteVideo$.next(handle);
    const view = service.videos$.value[0];
    expect(view).toEqual({
      handle,
      accessibleName: 'Autorisierte Remote-Kamera',
      state: 'active',
    });
    expect(JSON.stringify(view)).not.toMatch(/participant|publisher|publication|track[_-]?id/i);
    service.attach(view, document.createElement('video'));
    remoteVideoUnavailable$.next({ handleId: handle.handleId, reason: 'unsubscribed' });
    expect(release).toHaveBeenCalledOnce();
    expect(service.videos$.value).toEqual([]);
  });

  it('clears every row and attachment when the owned session disconnects', () => {
    remoteVideo$.next(Object.freeze({ handleId: 'sfu-video-2', source: 'screen' }));
    service.attach(service.videos$.value[0], document.createElement('video'));
    state$.next({ status: 'fallback' });
    expect(release).toHaveBeenCalledOnce();
    expect(service.videos$.value).toEqual([]);
  });
});
