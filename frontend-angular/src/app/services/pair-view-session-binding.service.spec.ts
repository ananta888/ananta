import { TestBed } from '@angular/core/testing';
import { BehaviorSubject } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { PairViewSessionBindingService } from './pair-view-session-binding.service';
import { PairViewSyncService } from './pair-view-sync.service';
import { ShareSessionService } from './share-session.service';
import { SharedViewStateService } from './shared-view-state.service';

describe('PairViewSessionBindingService', () => {
  it('binds the real Pair-Sync port and releases it with the Share lifecycle', () => {
    const state$ = new BehaviorSubject<any>({ session: null, participants: [], messages: [], cursor: '0', role: null });
    const securityState$ = new BehaviorSubject<any>({ status: 'waiting_for_peer' });
    const share = {
      state$, securityState$, currentUserId: 'alice',
      isStrictSession: (session: any) => session?.security_mode === 'strict_e2ee',
    };
    const sync = {
      bindSession: vi.fn(), updateSecurityEpoch: vi.fn(), onCryptoReady: vi.fn(), unbindSession: vi.fn(),
    };
    const view = { init: vi.fn() };
    TestBed.configureTestingModule({ providers: [
      { provide: ShareSessionService, useValue: share },
      { provide: PairViewSyncService, useValue: sync },
      { provide: SharedViewStateService, useValue: view },
    ] });
    const binding = TestBed.inject(PairViewSessionBindingService);
    binding.start();
    expect(view.init).toHaveBeenCalledOnce();
    state$.next({
      session: { id: 'session-a', security_epoch: 4, security_mode: 'strict_e2ee' },
      participants: [], messages: [], cursor: '0', role: 'owner',
    });
    expect(sync.bindSession).toHaveBeenCalledWith('session-a', 'alice', 4);
    securityState$.next({ status: 'ready', fingerprint: 'f'.repeat(64) });
    expect(sync.onCryptoReady).toHaveBeenCalled();
    state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
    expect(sync.unbindSession).toHaveBeenCalledOnce();
  });
});
