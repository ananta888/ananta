import { BehaviorSubject } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { PairViewSyncPanelComponent } from './pair-view-sync-panel.component';
import { DEFAULT_PERMISSIONS } from '../services/pair-view-sync.types';
import {
  ActiveShareState,
  ShareSession,
  ShareSessionService,
} from '../services/share-session.service';
import { PairViewSyncService } from '../services/pair-view-sync.service';
import { SharedViewStateService } from '../services/shared-view-state.service';
import { PairViewSecurityBootstrapService } from '../services/pair-view-security-bootstrap.service';

const ACTIVE_SESSION: ShareSession = {
  id: 'session-a',
  title: 'Pair A',
  invite_code: 'invite-a',
  mode: 'p2p',
  transport: 'webrtc',
  permissions: { chat: true },
  created_at: 1,
  expires_at: null,
  revoked_at: null,
  owner_user_id: 'owner',
  security_epoch: 3,
  security_contract_version: 1,
  security_mode: 'strict_e2ee',
};

describe('PairViewSyncPanelComponent', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [PairViewSyncPanelComponent],
      providers: [provideRouter([]), provideNoopAnimations()],
    });
  });

  it('mounts and shows the create form by default', () => {
    const fixture = TestBed.createComponent(PairViewSyncPanelComponent);
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('Pair-Dev View-Sync');
    expect(text).toContain('Berechtigungen');
  });

  it('renders a checkbox for every documented permission', () => {
    const fixture = TestBed.createComponent(PairViewSyncPanelComponent);
    fixture.detectChanges();
    const checkboxes = (fixture.nativeElement as HTMLElement).querySelectorAll('input[type=checkbox]');
    expect(checkboxes.length).toBe(5);
  });

  it('uses the canonical Hub permission names and safe defaults', () => {
    const fixture = TestBed.createComponent(PairViewSyncPanelComponent);
    const cmp = fixture.componentInstance;
    expect(cmp.form.selected.chat).toBe(true);
    expect(cmp.form.selected.view_tui).toBe(true);
    expect(cmp.form.selected.artifact_share).toBe(true);
    expect(cmp.form.selected.remote_control).toBe(false);
    expect(cmp.form.selected.remote_cursor).toBe(false);
  });

  it('preserves the same default as the shared service default', () => {
    const fixture = TestBed.createComponent(PairViewSyncPanelComponent);
    const cmp = fixture.componentInstance;
    const d = DEFAULT_PERMISSIONS;
    expect(cmp.form.selected.chat).toBe(d.chat);
    expect(cmp.form.selected.view_tui).toBe(d.view_tui);
    expect(cmp.form.selected.remote_control).toBe(d.remote_control);
    expect(cmp.form.selected.remote_cursor).toBe(d.remote_cursor);
    expect(cmp.form.selected.artifact_share).toBe(d.artifact_share);
  });

  it('keeps a failed owner end visible and retryable before unbinding', async () => {
    const first = deferred<void>();
    const harness = activeHarness('owner', {
      endSession: vi.fn()
        .mockReturnValueOnce(first.promise)
        .mockResolvedValueOnce(undefined),
    });
    const action = harness.component.onEnd();
    harness.fixture.detectChanges();

    const button = harness.fixture.nativeElement.querySelector('.end-row button') as HTMLButtonElement;
    expect(harness.component.busy()).toBe(true);
    expect(button.disabled).toBe(true);
    expect(button.textContent).toContain('wird beendet');

    first.reject({ status: 503, error: { error: 'service_unavailable' } });
    await action;
    harness.fixture.detectChanges();

    expect(harness.component.busy()).toBe(false);
    expect(harness.component.error()).toBe('service_unavailable');
    expect(harness.sync.unbindSession).not.toHaveBeenCalled();
    expect(harness.security.clear).not.toHaveBeenCalled();
    const alert = harness.fixture.nativeElement.querySelector(
      '[data-testid="pair-session-action-error"]',
    ) as HTMLElement;
    expect(alert.hidden).toBe(false);
    expect(alert.textContent).toContain('service_unavailable');

    await harness.component.onEnd();
    expect(harness.share.endSession).toHaveBeenCalledTimes(2);
    expect(harness.sync.unbindSession).toHaveBeenCalledOnce();
    expect(harness.security.clear).toHaveBeenCalledOnce();
  });

  it('shows a terminal participant rejection and unbinds its retired local state', async () => {
    const failure = { status: 403, error: { error: 'forbidden' } };
    const harness = activeHarness('participant');
    vi.mocked(harness.share.leaveSession).mockImplementationOnce(async () => {
      harness.share.state$.next({
        session: null,
        participants: [],
        messages: [],
        cursor: '0',
        role: null,
      });
      throw failure;
    });

    await harness.component.onEnd();

    expect(harness.share.leaveSession).toHaveBeenCalledOnce();
    expect(harness.share.endSession).not.toHaveBeenCalled();
    expect(harness.component.error()).toBe('forbidden');
    expect(harness.component.activeSession()).toBeNull();
    expect(harness.sync.unbindSession).toHaveBeenCalledOnce();
    expect(harness.security.clear).toHaveBeenCalledOnce();
  });

  it('does not let an old end completion unbind a replacement session', async () => {
    const first = deferred<void>();
    const harness = activeHarness('owner', {
      endSession: vi.fn().mockReturnValue(first.promise),
    });
    const action = harness.component.onEnd();
    harness.share.state$.next({
      session: { ...ACTIVE_SESSION, id: 'session-b', title: 'Pair B', invite_code: 'invite-b' },
      participants: [],
      messages: [],
      cursor: '0',
      role: 'owner',
    });
    first.resolve(undefined);
    await action;

    expect(harness.component.activeSession()?.id).toBe('session-b');
    expect(harness.sync.unbindSession).not.toHaveBeenCalled();
    expect(harness.security.clear).not.toHaveBeenCalled();
  });

  it('does not let an old end rejection unbind or overwrite a replacement session', async () => {
    const first = deferred<void>();
    const harness = activeHarness('owner', {
      endSession: vi.fn().mockReturnValue(first.promise),
    });
    const action = harness.component.onEnd();
    harness.share.state$.next({
      session: { ...ACTIVE_SESSION, id: 'session-b', title: 'Pair B', invite_code: 'invite-b' },
      participants: [],
      messages: [],
      cursor: '0',
      role: 'owner',
    });
    first.reject({ status: 403, error: { error: 'forbidden' } });
    await action;

    expect(harness.component.activeSession()?.id).toBe('session-b');
    expect(harness.component.error()).toBe('');
    expect(harness.sync.unbindSession).not.toHaveBeenCalled();
    expect(harness.security.clear).not.toHaveBeenCalled();
  });
});

function activeHarness(
  role: 'owner' | 'participant',
  actions: {
    endSession?: ReturnType<typeof vi.fn>;
    leaveSession?: ReturnType<typeof vi.fn>;
  } = {},
) {
  const state$ = new BehaviorSubject<ActiveShareState>({
    session: { ...ACTIVE_SESSION },
    participants: [],
    messages: [],
    cursor: '0',
    role,
  });
  const share = {
    state$,
    currentUserId: role === 'owner' ? 'owner' : 'participant',
    createSession: vi.fn(),
    endSession: actions.endSession ?? vi.fn().mockResolvedValue(undefined),
    leaveSession: actions.leaveSession ?? vi.fn().mockResolvedValue(undefined),
    participantStatus: vi.fn(() => 'online'),
    hasPermission: vi.fn(() => false),
  };
  const sync = {
    bindSession: vi.fn(),
    unbindSession: vi.fn(),
    updateSecurityEpoch: vi.fn(),
    onCryptoReady: vi.fn(),
    getFollowMode: vi.fn(() => 'active'),
    setFollowMode: vi.fn(),
    requestControl: vi.fn(),
    hasControlGrant: vi.fn(() => false),
  };
  const security = {
    state$: new BehaviorSubject({ status: 'idle' as const }),
    currentEpoch: 3,
    ensure: vi.fn(async () => false),
    approveFingerprintChange: vi.fn(),
    clear: vi.fn(),
  };
  TestBed.overrideProvider(ShareSessionService, { useValue: share });
  TestBed.overrideProvider(PairViewSyncService, { useValue: sync });
  TestBed.overrideProvider(SharedViewStateService, {
    useValue: { state$: new BehaviorSubject(null) },
  });
  TestBed.overrideProvider(PairViewSecurityBootstrapService, { useValue: security });
  const fixture = TestBed.createComponent(PairViewSyncPanelComponent);
  fixture.detectChanges();
  return { fixture, component: fixture.componentInstance, share, sync, security };
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}
