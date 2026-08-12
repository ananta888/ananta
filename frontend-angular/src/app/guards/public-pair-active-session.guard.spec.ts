import { TestBed } from '@angular/core/testing';
import { ActivatedRouteSnapshot, RouterStateSnapshot } from '@angular/router';

import type { PublicPairPageComponent } from '../features/pair/public-pair-page.component';
import { NotificationService } from '../services/notification.service';
import { ShareSessionService } from '../services/share-session.service';
import { publicPairActiveSessionGuard } from './public-pair-active-session.guard';

describe('publicPairActiveSessionGuard', () => {
  const state = {
    session: null as { id: string } | null,
    participants: [],
    messages: [],
    cursor: '0',
    role: null as 'owner' | 'participant' | null,
  };
  const shares = {
    state$: { value: state },
    get isActive() { return state.session !== null; },
    sessionMutationPending: false,
    leaveSession: vi.fn(async () => {
      state.session = null;
      state.role = null;
    }),
  };
  const notifications = {
    error: vi.fn(),
    fromApiError: vi.fn((_error: unknown, fallback: string) => fallback),
  };

  beforeEach(() => {
    state.session = null;
    state.role = null;
    shares.sessionMutationPending = false;
    shares.leaveSession.mockClear();
    notifications.error.mockClear();
    notifications.fromApiError.mockClear();
    vi.stubGlobal('confirm', vi.fn(() => true));
    TestBed.configureTestingModule({
      providers: [
        { provide: ShareSessionService, useValue: shares },
        { provide: NotificationService, useValue: notifications },
      ],
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    TestBed.resetTestingModule();
  });

  it('allows navigation without prompting when no Pair session is active', async () => {
    expect(await invokeGuard()).toBe(true);
    expect(globalThis.confirm).not.toHaveBeenCalled();
    expect(shares.leaveSession).not.toHaveBeenCalled();
  });

  it('blocks route teardown while create or join is still in flight', async () => {
    shares.sessionMutationPending = true;

    expect(await invokeGuard()).toBe(false);
    expect(globalThis.confirm).not.toHaveBeenCalled();
    expect(shares.leaveSession).not.toHaveBeenCalled();
    expect(notifications.error).toHaveBeenCalledWith(
      'Die laufende Session-Erstellung wird noch abgeschlossen. Pair Dev bleibt geöffnet.',
    );
  });

  it('keeps an active Pair session mounted when navigation is cancelled', async () => {
    state.session = { id: 'session-a' };
    state.role = 'owner';
    vi.mocked(globalThis.confirm).mockReturnValue(false);

    expect(await invokeGuard()).toBe(false);
    expect(shares.leaveSession).not.toHaveBeenCalled();
    expect(state.session?.id).toBe('session-a');
  });

  it('blocks teardown when the active session has no authoritative local role', async () => {
    state.session = { id: 'session-a' };
    state.role = null;

    expect(await invokeGuard()).toBe(false);
    expect(globalThis.confirm).not.toHaveBeenCalled();
    expect(shares.leaveSession).not.toHaveBeenCalled();
    expect(notifications.error).toHaveBeenCalledWith(
      'Die aktive Pair-Session hat keinen gültigen lokalen Rollenbezug.',
    );
  });

  it('retires the active membership before allowing route teardown', async () => {
    state.session = { id: 'session-a' };
    state.role = 'participant';

    expect(await invokeGuard()).toBe(true);
    expect(shares.leaveSession).toHaveBeenCalledOnce();
    expect(state.session).toBeNull();
  });

  it('blocks teardown when a replacement session becomes active during retirement', async () => {
    state.session = { id: 'session-a' };
    state.role = 'participant';
    shares.leaveSession.mockImplementationOnce(async () => {
      state.session = { id: 'session-b' };
      state.role = 'owner';
    });

    expect(await invokeGuard()).toBe(false);
    expect(state.session?.id).toBe('session-b');
  });

  it('blocks navigation and reports a retirement failure', async () => {
    state.session = { id: 'session-a' };
    state.role = 'owner';
    shares.leaveSession.mockRejectedValueOnce(new Error('offline'));

    expect(await invokeGuard()).toBe(false);
    expect(notifications.error).toHaveBeenCalledWith(
      'Pair Dev konnte nicht sicher beendet werden. Die Seite bleibt geöffnet.',
    );
    expect(state.session?.id).toBe('session-a');
  });
});

async function invokeGuard(): Promise<boolean> {
  return TestBed.runInInjectionContext(async () => {
    const result = publicPairActiveSessionGuard(
      {} as PublicPairPageComponent,
      {} as ActivatedRouteSnapshot,
      { url: '/pair-dev' } as RouterStateSnapshot,
      { url: '/voice' } as RouterStateSnapshot,
    );
    return await Promise.resolve(result as boolean | Promise<boolean>);
  });
}
