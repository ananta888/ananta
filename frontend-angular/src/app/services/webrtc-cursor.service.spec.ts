import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { ShareSessionService } from './share-session.service';
import { SnakeOverlayService } from './snake-overlay.service';
import { WebrtcCursorService } from './webrtc-cursor.service';
import { WebrtcTransportService } from './webrtc-transport.service';

describe('WebrtcCursorService authority boundary', () => {
  const mode$ = new BehaviorSubject<'idle' | 'webrtc' | 'hub_relay'>('webrtc');
  const messages$ = new Subject<{ type: string; payload: unknown }>();
  const shareState$ = new BehaviorSubject<any>({ session: { id: 'public-session' } });
  const visible$ = new BehaviorSubject(true);
  const send = vi.fn();
  const setRemoteCursor = vi.fn();
  const authorityKindForSession = vi.fn(() => 'public' as 'public' | 'hub');
  let service: WebrtcCursorService;

  beforeEach(() => {
    vi.useFakeTimers();
    send.mockReset();
    setRemoteCursor.mockReset();
    authorityKindForSession.mockReset();
    authorityKindForSession.mockReturnValue('public');
    shareState$.next({ session: { id: 'public-session' } });
    visible$.next(true);
    mode$.next('webrtc');
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      WebrtcCursorService,
      { provide: WebrtcTransportService, useValue: { mode$, message$: messages$, send } },
      { provide: ShareSessionService, useValue: { state$: shareState$, currentUserId: 'alice' } },
      { provide: SnakeOverlayService, useValue: {
        visible$, setRemoteCursor, remoteCursors$: new BehaviorSubject(new Map()),
      } },
      { provide: PairSessionControlPlaneService, useValue: { authorityKindForSession } },
    ] });
    service = TestBed.inject(WebrtcCursorService);
  });

  afterEach(() => {
    service.ngOnDestroy();
    vi.useRealTimers();
  });

  it('never sends or renders legacy raw cursors for public Pair', () => {
    vi.advanceTimersByTime(250);
    messages$.next({ type: 'cursor', payload: { sender_id: 'bob', x: 0.2, y: 0.3 } });

    expect(send).not.toHaveBeenCalled();
    expect(setRemoteCursor).not.toHaveBeenCalled();
  });

  it('preserves the legacy cursor path for an explicitly bound Hub session', () => {
    authorityKindForSession.mockReturnValue('hub');
    shareState$.next({ session: { id: 'hub-session' } });
    vi.advanceTimersByTime(60);
    messages$.next({ type: 'cursor', payload: { sender_id: 'bob', x: 0.2, y: 0.3 } });

    expect(send).toHaveBeenCalledWith('cursor', expect.objectContaining({ sender_id: 'alice' }));
    expect(setRemoteCursor).toHaveBeenCalledWith('bob', expect.any(Number), expect.any(Number));
  });
});
