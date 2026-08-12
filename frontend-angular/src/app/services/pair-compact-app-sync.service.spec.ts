import { DOCUMENT } from '@angular/common';
import { TestBed } from '@angular/core/testing';
import { BehaviorSubject } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PairCompactAppSyncService, compactPeerLabel } from './pair-compact-app-sync.service';
import { PairViewSyncService } from './pair-view-sync.service';
import { ShareSessionService } from './share-session.service';
import { SharedViewStateService } from './shared-view-state.service';
import { PairViewSessionBindingService } from './pair-view-session-binding.service';

describe('PairCompactAppSyncService', () => {
  const init = vi.fn();
  const sendCursor = vi.fn();
  const updateScroll = vi.fn();
  const bindingStart = vi.fn();
  const publicPairRuntimeState$ = new BehaviorSubject<any>('idle');
  const localCompactSharing$ = new BehaviorSubject({ view: false, cursor: false });
  let service: PairCompactAppSyncService;

  beforeEach(() => {
    init.mockReset();
    sendCursor.mockReset();
    updateScroll.mockReset();
    bindingStart.mockReset();
    publicPairRuntimeState$.next('idle');
    localCompactSharing$.next({ view: false, cursor: false });
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      PairCompactAppSyncService,
      { provide: SharedViewStateService, useValue: { init, updateScroll } },
      { provide: PairViewSyncService, useValue: { sendCursor, localCompactSharing$ } },
      { provide: PairViewSessionBindingService, useValue: { start: bindingStart } },
      { provide: ShareSessionService, useValue: {
        publicPairRuntimeState$, currentUserId: 'member:alice:private-id',
      } },
      { provide: DOCUMENT, useValue: document },
    ] });
    service = TestBed.inject(PairCompactAppSyncService);
  });

  it('captures only bounded numeric viewport scroll while local view sharing is enabled', () => {
    service.start();
    window.dispatchEvent(new Event('scroll'));
    expect(updateScroll).not.toHaveBeenCalled();

    publicPairRuntimeState$.next('public');
    Object.defineProperty(window, 'scrollX', { configurable: true, value: 12 });
    Object.defineProperty(window, 'scrollY', { configurable: true, value: 34 });
    localCompactSharing$.next({ view: true, cursor: false });

    expect(updateScroll).toHaveBeenCalledWith({ x: 12, y: 34 });
  });

  it('starts route capture once and emits only normalised Public Pair pointers', () => {
    service.start();
    service.start();
    expect(init).toHaveBeenCalledOnce();
    expect(bindingStart).toHaveBeenCalledOnce();

    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 40, clientY: 25 }));
    expect(sendCursor).not.toHaveBeenCalled();

    publicPairRuntimeState$.next('public');
    localCompactSharing$.next({ view: false, cursor: true });
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 40, clientY: 25 }));
    expect(sendCursor).toHaveBeenCalledWith(
      compactPeerLabel('member:alice:private-id'),
      expect.objectContaining({
        line: null,
        column: null,
        nx: 40 / window.innerWidth,
        ny: 25 / window.innerHeight,
      }),
    );
  });

  it('derives a bounded non-secret display label', () => {
    const label = compactPeerLabel('member:alice:very-private-stable-id');
    expect(label).toMatch(/^Peer-[0-9a-f]{6}$/);
    expect(label).not.toContain('alice');
  });
});
