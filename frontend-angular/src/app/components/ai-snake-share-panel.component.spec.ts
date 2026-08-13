import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, of } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AiSnakeSharePanelComponent } from './ai-snake-share-panel.component';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiCoreService } from '../services/hub-api-core.service';
import { PairViewSessionBindingService } from '../services/pair-view-session-binding.service';
import { ShareSessionService } from '../services/share-session.service';
import { PairViewSyncService } from '../services/pair-view-sync.service';

function configure(status: 'ready' | 'confirming' | 'legacy') {
  TestBed.resetTestingModule();
  const securityState$ = new BehaviorSubject<any>({ status, fingerprint: status === 'ready' ? 'f'.repeat(64) : undefined });
  const state$ = new BehaviorSubject<any>({
    session: {
      id: 'session-a', title: 'Reachable Strict Pair', invite_code: 'invite', permissions: { chat: true },
      security_contract_version: status === 'legacy' ? 0 : 1,
      security_mode: status === 'legacy' ? 'legacy' : 'strict_e2ee',
    },
    participants: [], messages: [], cursor: '0', role: 'owner',
  });
  const service = {
    state$, securityState$, isActive: true, sessionMutationPending: false, currentUserId: 'alice',
    canSendChat: () => securityState$.value.status === 'ready' || securityState$.value.status === 'legacy',
    sendMessage: vi.fn(async () => undefined), approveFingerprintChange: vi.fn(),
    participantStatus: () => 'online', endSession: vi.fn(), leaveSession: vi.fn(),
    revokeParticipant: vi.fn(), createSession: vi.fn(async () => ({ id: 'session-new', security_epoch: 4 })), joinSession: vi.fn(),
    listSessions: vi.fn(async () => []), switchToSession: vi.fn(async () => undefined),
    discardPendingJoinAttempt: vi.fn(),
  };
  const binding = { start: vi.fn() };
  const pairSync = {
    remoteViews$: new BehaviorSubject(new Map()),
    isLocalViewSharingEnabled: false,
    isLocalCursorSharingEnabled: false,
    isLocalCompactSharingPending: false,
    setLocalCompactSharing: vi.fn(() => true),
    armLocalCompactSharingOnFirstPeerReady: vi.fn(() => true),
    cancelPendingLocalCompactSharing: vi.fn(() => true),
  };
  const core = {
    get: vi.fn(() => of({})),
    post: vi.fn(() => of({})),
    delete: vi.fn(() => of({})),
  };
  TestBed.configureTestingModule({
    imports: [AiSnakeSharePanelComponent],
    providers: [
      { provide: ShareSessionService, useValue: service },
      { provide: PairViewSessionBindingService, useValue: binding },
      { provide: PairViewSyncService, useValue: pairSync },
      { provide: AgentDirectoryService, useValue: { list: () => [] } },
      { provide: HubApiCoreService, useValue: core },
    ],
  });
  return { service, binding, core, pairSync };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe('AiSnakeSharePanelComponent production security host', () => {
  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('boots the Pair binding and exposes confirmed E2EE in the reachable Share UI', () => {
    const { binding } = configure('ready');
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.detectChanges();
    expect(binding.start).toHaveBeenCalledOnce();
    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('[data-testid="share-security-status"]')?.textContent).toContain('Peer und Schlüssel bestätigt');
    expect((host.querySelector('.share-chat-input') as HTMLInputElement).disabled).toBe(false);
  });

  it('disables chat while confirmation is pending and labels Legacy explicitly', async () => {
    const { service } = configure('confirming');
    const pending = TestBed.createComponent(AiSnakeSharePanelComponent);
    pending.detectChanges();
    await pending.whenStable();
    pending.detectChanges();
    expect(service.canSendChat()).toBe(false);
    expect(pending.componentInstance.canChat(service.state$.value)).toBe(false);
    expect((pending.nativeElement as HTMLElement).querySelector('.share-chat-input')).toHaveProperty('disabled', true);

    configure('legacy');
    const legacy = TestBed.createComponent(AiSnakeSharePanelComponent);
    legacy.detectChanges();
    expect((legacy.nativeElement as HTMLElement).textContent).toContain('Legacy-Modus: nicht Ende-zu-Ende verschlüsselt');
  });

  it('hides and fences every Hub-backed group operation in public-only mode', () => {
    const { core } = configure('ready');
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const group = { id: 'group-a', name: 'Hub group' } as never;
    const member = { id: 'member-a', user_id: 'hub-user' } as never;

    expect((fixture.nativeElement as HTMLElement).textContent).not.toContain('Gruppen');

    component.switchToGroups();
    component.startCreateGroup();
    component.newGroupName = 'blocked';
    component.doCreateGroup();
    component.openGroup(group);
    component.newMemberId = 'blocked-user';
    component.addMember();
    component.removeMember(member);
    component.deleteGroup(group);
    component.createGroupSession(group);

    expect(component.mainTab).toBe('share');
    expect(core.get).not.toHaveBeenCalled();
    expect(core.post).not.toHaveBeenCalled();
    expect(core.delete).not.toHaveBeenCalled();
  });

  it('pins create and join mutations to the Public authority in public-only mode', async () => {
    const { service, pairSync } = configure('ready');
    service.isActive = false;
    service.state$.next({
      session: null, participants: [], messages: [], cursor: '0', role: null,
    });
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    const component = fixture.componentInstance;
    component.createTitle = 'Public Pair';
    component.joinCode = 'PUBLIC42';

    await component.doCreate();
    await component.doJoin();

    expect(service.createSession).toHaveBeenCalledWith(
      'Public Pair',
      expect.any(Object),
      expect.any(Number),
      { expectedAuthority: 'public' },
    );
    expect(service.joinSession).toHaveBeenCalledWith('PUBLIC42', {
      allowLegacy: false,
      expectedAuthority: 'public',
    });
  });

  it('creates the pixel-free compact Public share with one click', async () => {
    const { service, pairSync } = configure('ready');
    service.isActive = false;
    service.state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector<HTMLButtonElement>('[data-testid="quick-compact-pair-share"]');
    expect(button).not.toBeNull();
    expect(fixture.nativeElement.textContent).toContain('keine Bildschirmpixel');
    button?.click();
    await fixture.whenStable();

    expect(service.createSession).toHaveBeenCalledWith(
      'Ananta-App gemeinsam ansehen',
      {
        chat: true,
        view_tui: true,
        remote_cursor: true,
        artifact_share: false,
        remote_control: false,
      },
      3600,
      { expectedAuthority: 'public' },
    );
    expect(pairSync.armLocalCompactSharingOnFirstPeerReady).toHaveBeenCalledWith('session-new');
    expect(pairSync.setLocalCompactSharing).not.toHaveBeenCalled();
  });

  it('toggles compact sharing on the active eligible session without creating another one', async () => {
    const { service, pairSync } = configure('ready');
    service.state$.next({
      ...service.state$.value,
      session: {
        ...service.state$.value.session,
        security_epoch: 7,
        permissions: { chat: true, view_tui: true, remote_cursor: true },
      },
    });
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector<HTMLButtonElement>('[data-testid="quick-compact-pair-share"]');
    expect(button?.textContent).toContain('Ananta-App schnell teilen');
    button?.click();
    await fixture.whenStable();

    expect(pairSync.setLocalCompactSharing).toHaveBeenCalledWith(
      'session-a', 7, { view: true, cursor: true },
    );
    expect(service.createSession).not.toHaveBeenCalled();
    expect(service.endSession).not.toHaveBeenCalled();
    expect(service.leaveSession).not.toHaveBeenCalled();
  });

  it('uses the same active button to stop sharing', async () => {
    const { service, pairSync } = configure('ready');
    service.state$.next({
      ...service.state$.value,
      session: {
        ...service.state$.value.session,
        security_epoch: 7,
        permissions: { chat: true, view_tui: true, remote_cursor: true },
      },
    });
    pairSync.isLocalViewSharingEnabled = true;
    pairSync.isLocalCursorSharingEnabled = true;
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector<HTMLButtonElement>('[data-testid="quick-compact-pair-share"]');
    expect(button?.textContent).toContain('nicht mehr teilen');
    button?.click();
    await fixture.whenStable();
    expect(pairSync.setLocalCompactSharing).toHaveBeenCalledWith(
      'session-a', 7, { view: false, cursor: false },
    );
    expect(service.createSession).not.toHaveBeenCalled();
  });

  it('uses the same active button to revoke a pending first-peer intent', () => {
    const { service, pairSync } = configure('confirming');
    service.state$.next({
      ...service.state$.value,
      session: {
        ...service.state$.value.session,
        security_epoch: 7,
        permissions: { chat: true, view_tui: true, remote_cursor: true },
      },
    });
    pairSync.isLocalCompactSharingPending = true;
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector<HTMLButtonElement>('[data-testid="quick-compact-pair-share"]');
    expect(button?.textContent).toContain('abbrechen');
    button?.click();

    expect(pairSync.cancelPendingLocalCompactSharing).toHaveBeenCalledWith('session-a');
    expect(pairSync.setLocalCompactSharing).not.toHaveBeenCalled();
    expect(service.createSession).not.toHaveBeenCalled();
  });

  it('arms the active owner session before its first peer establishes the final epoch', async () => {
    const { service, pairSync } = configure('confirming');
    service.state$.next({
      ...service.state$.value,
      session: {
        ...service.state$.value.session,
        security_epoch: null,
        permissions: { chat: true, view_tui: true, remote_cursor: true },
      },
      role: 'owner',
    });
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    fixture.detectChanges();

    fixture.nativeElement.querySelector<HTMLButtonElement>('[data-testid="quick-compact-pair-share"]')?.click();
    await fixture.whenStable();

    expect(pairSync.armLocalCompactSharingOnFirstPeerReady).toHaveBeenCalledWith('session-a');
    expect(pairSync.setLocalCompactSharing).not.toHaveBeenCalled();
    expect(service.createSession).not.toHaveBeenCalled();
  });

  it('creates a new quick-share session while preserving an active session without compact permissions', async () => {
    const { service, pairSync } = configure('ready');
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const button = host.querySelector<HTMLButtonElement>('[data-testid="quick-compact-pair-share"]');
    expect(button?.disabled).toBe(false);
    expect(button?.textContent).toContain('Neue Schnellteilen-Session');
    expect(host.querySelector('[data-testid="quick-share-parks-current"]')?.textContent).toContain('geparkt');

    button?.click();
    await fixture.whenStable();

    expect(service.createSession).toHaveBeenCalledWith(
      'Ananta-App gemeinsam ansehen',
      expect.objectContaining({ view_tui: true, remote_cursor: true }),
      3600,
      { expectedAuthority: 'public' },
    );
    expect(pairSync.armLocalCompactSharingOnFirstPeerReady).toHaveBeenCalledWith('session-new');
    expect(service.endSession).not.toHaveBeenCalled();
    expect(service.leaveSession).not.toHaveBeenCalled();
  });

  it('keeps create, join and the session catalogue reachable while a session is active', () => {
    const { service } = configure('ready');
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    fixture.detectChanges();
    const host = fixture.nativeElement as HTMLElement;

    expect(host.querySelector('[data-testid="manage-pair-sessions"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="create-pair-session"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="join-pair-session"]')).not.toBeNull();

    host.querySelector<HTMLButtonElement>('[data-testid="manage-pair-sessions"]')?.click();
    fixture.detectChanges();
    expect(host.querySelector('[data-testid="pair-session-catalog"]')).not.toBeNull();
    expect(service.endSession).not.toHaveBeenCalled();
    expect(service.leaveSession).not.toHaveBeenCalled();
  });

  it('handles an active catalogue open intent with chat and the owner invite but no lifecycle mutation', () => {
    const { service } = configure('ready');
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    fixture.componentInstance.view = 'catalog';
    fixture.componentInstance.activeTab = 'participants';
    fixture.componentInstance.onSessionOpened();
    fixture.detectChanges();

    expect(fixture.componentInstance.view).toBe('home');
    expect(fixture.componentInstance.activeTab).toBe('chat');
    expect(host.querySelector('.share-session-title')?.textContent).toContain('Reachable Strict Pair');
    expect(host.querySelector('.share-meta-code')?.textContent).toContain('invite');
    expect(host.querySelector('.share-chat-input')).not.toBeNull();
    expect(service.switchToSession).not.toHaveBeenCalled();
    expect(service.endSession).not.toHaveBeenCalled();
    expect(service.leaveSession).not.toHaveBeenCalled();
  });

  it('does not expose an invitation code in a participant session view', () => {
    const { service } = configure('ready');
    service.state$.next({ ...service.state$.value, role: 'participant' });
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.share-meta-code')).toBeNull();
  });

  it('fences toolbar methods while a create, join or switch mutation owns the session state', async () => {
    const { service, pairSync } = configure('ready');
    service.sessionMutationPending = true;
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentRef.setInput('publicOnly', true);
    const component = fixture.componentInstance;

    component.toggleCatalog();
    component.openCreate();
    component.openJoin();
    await component.doQuickCompactShare();

    expect(component.view).toBe('home');
    expect(service.createSession).not.toHaveBeenCalled();
    expect(pairSync.setLocalCompactSharing).not.toHaveBeenCalled();
    expect(pairSync.armLocalCompactSharingOnFirstPeerReady).not.toHaveBeenCalled();
  });

  it('shows and revokes the pending one-shot compact-share intent', () => {
    const { service, pairSync } = configure('ready');
    service.state$.next({
      ...service.state$.value,
      session: {
        ...service.state$.value.session,
        security_epoch: 1,
        permissions: { chat: true, view_tui: true, remote_cursor: true },
      },
    });
    pairSync.isLocalCompactSharingPending = true;
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.detectChanges();

    const cancel = fixture.nativeElement.querySelector<HTMLButtonElement>(
      '[data-testid="cancel-pending-compact-share"]',
    );
    expect(cancel?.textContent).toContain('widerrufen');
    cancel?.click();
    expect(pairSync.cancelPendingLocalCompactSharing).toHaveBeenCalledWith('session-a');
    expect(fixture.nativeElement.querySelector('[data-testid="toggle-own-compact-view"]')).toBeNull();
  });

  it('shows only authenticated remote compact page projections', () => {
    const { pairSync } = configure('ready');
    pairSync.remoteViews$.next(new Map([['peer-secret-id', {
      senderUserId: 'peer-secret-id',
      receivedAt: Date.now(),
      state: { route: '/workspace', activeSurface: 'unknown' },
    }]]));
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.detectChanges();
    const text = fixture.nativeElement.querySelector('[data-testid="compact-pair-peer-state"]')?.textContent ?? '';
    expect(text).toContain('/workspace');
    expect(text).toContain('kein automatisches Folgen');
    expect(text).not.toContain('peer-secret-id');
  });

  it('keeps manual/joined compact sharing off until each local toggle is used', () => {
    const { service, pairSync } = configure('ready');
    service.state$.next({
      ...service.state$.value,
      session: {
        ...service.state$.value.session,
        security_epoch: 7,
        permissions: { chat: true, view_tui: true, remote_cursor: true },
      },
      role: 'participant',
    });
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.detectChanges();

    expect(pairSync.setLocalCompactSharing).not.toHaveBeenCalled();
    fixture.nativeElement.querySelector<HTMLButtonElement>('[data-testid="toggle-own-compact-view"]')?.click();
    expect(pairSync.setLocalCompactSharing).toHaveBeenCalledWith(
      'session-a', 7, { view: true, cursor: false },
    );
    fixture.nativeElement.querySelector<HTMLButtonElement>('[data-testid="toggle-own-compact-cursor"]')?.click();
    expect(pairSync.setLocalCompactSharing).toHaveBeenLastCalledWith(
      'session-a', 7, { view: false, cursor: true },
    );
  });

  it('discards only a conflicted pending join after explicit confirmation', async () => {
    const { service } = configure('ready');
    service.isActive = false;
    service.state$.next({
      session: null, participants: [], messages: [], cursor: '0', role: null,
    });
    const pendingCapability = 'C'.repeat(43);
    const previousBody = 'previous-private-request-body';
    const bearerToken = 'private-oidc-bearer-token';
    service.joinSession.mockRejectedValueOnce(Object.assign(
      new Error('public_pair_pending_attempt_conflict'),
      { pendingCapability, previousBody, bearerToken },
    ));
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    const component = fixture.componentInstance;
    component.view = 'join';
    component.joinCode = 'CURRENT42';
    fixture.detectChanges();
    const log = vi.spyOn(console, 'log').mockImplementation(() => undefined);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    await component.doJoin();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const renderedText = host.textContent ?? '';
    expect(renderedText).toContain('früherer Beitrittsversuch');
    expect(renderedText).not.toContain('public_pair_pending_attempt_conflict');
    expect(renderedText).not.toContain(pendingCapability);
    expect(renderedText).not.toContain(previousBody);
    expect(renderedText).not.toContain(bearerToken);
    expect(log).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();
    expect(component.joinCode).toBe('CURRENT42');

    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    const recovery = host.querySelector<HTMLButtonElement>('[data-testid="discard-pending-join"]');
    expect(recovery).not.toBeNull();

    recovery?.click();
    expect(confirm).toHaveBeenCalledOnce();
    expect(service.discardPendingJoinAttempt).not.toHaveBeenCalled();
    expect(component.pendingJoinRecoveryAvailable).toBe(true);
    expect(component.joinCode).toBe('CURRENT42');

    recovery?.click();
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(service.discardPendingJoinAttempt).toHaveBeenCalledOnce();
    expect(component.pendingJoinRecoveryAvailable).toBe(false);
    expect(component.joinCode).toBe('CURRENT42');
    fixture.detectChanges();
    expect(host.querySelector('[data-testid="discard-pending-join"]')).toBeNull();
    expect(log).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();
    confirm.mockRestore();
    log.mockRestore();
    warn.mockRestore();
    consoleError.mockRestore();
  });

  it('does not offer or run recovery for an unknown join failure', async () => {
    const { service } = configure('ready');
    service.isActive = false;
    service.state$.next({
      session: null, participants: [], messages: [], cursor: '0', role: null,
    });
    service.joinSession.mockRejectedValueOnce(new Error('unknown_pending_state'));
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.componentInstance.view = 'join';
    fixture.componentInstance.joinCode = 'CURRENT42';
    fixture.detectChanges();

    await fixture.componentInstance.doJoin();
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[data-testid="discard-pending-join"]')).toBeNull();
    expect(service.discardPendingJoinAttempt).not.toHaveBeenCalled();
    expect(fixture.componentInstance.joinCode).toBe('CURRENT42');
  });

  it('serializes owner end and leaves a failed terminal action retryable', async () => {
    const { service } = configure('ready');
    const pending = deferred<void>();
    service.endSession.mockReturnValueOnce(pending.promise);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.detectChanges();

    const endButton = fixture.nativeElement.querySelector<HTMLButtonElement>(
      '[data-testid="end-pair-session"]',
    );
    expect(endButton).not.toBeNull();
    endButton?.click();
    endButton?.click();
    fixture.changeDetectorRef.detectChanges();
    expect(service.endSession).toHaveBeenCalledOnce();
    expect(confirm).toHaveBeenCalledOnce();
    expect(endButton).toHaveProperty('disabled', true);

    pending.reject(new Error('pair_session_end_retry_required'));
    await pending.promise.catch(() => undefined);
    await Promise.resolve();
    fixture.changeDetectorRef.detectChanges();

    expect(fixture.nativeElement.querySelector('[data-testid="pair-session-action-error"]')?.textContent)
      .toContain('pair_session_end_retry_required');
    expect(fixture.nativeElement.querySelector('[data-testid="end-pair-session"]'))
      .toHaveProperty('disabled', false);

    service.endSession.mockResolvedValueOnce(undefined);
    await fixture.componentInstance.doEnd();
    expect(service.endSession).toHaveBeenCalledTimes(2);
    expect(fixture.componentInstance.sessionActionError).toBe('');
    confirm.mockRestore();
  });

  it('awaits the authenticated participant leave operation and renders its local error', async () => {
    const { service } = configure('ready');
    service.state$.next({ ...service.state$.value, role: 'participant' });
    service.leaveSession.mockRejectedValueOnce(new Error('pair_session_leave_retry_required'));
    const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
    fixture.detectChanges();

    await fixture.componentInstance.doLeave();
    fixture.detectChanges();

    expect(service.leaveSession).toHaveBeenCalledOnce();
    expect(fixture.nativeElement.querySelector('[data-testid="pair-session-action-error"]')?.textContent)
      .toContain('pair_session_leave_retry_required');
    expect(fixture.nativeElement.querySelector('[data-testid="leave-pair-session"]'))
      .toHaveProperty('disabled', false);
  });

  it.each(['create', 'join'] as const)(
    'returns from the %s sub-view to show a terminal session-action failure',
    async view => {
      const { service } = configure('ready');
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
      service.endSession.mockImplementationOnce(async () => {
        service.isActive = false;
        service.state$.next({
          session: null, participants: [], messages: [], cursor: '0', role: null,
        });
        throw new Error('membership_capability_retired');
      });
      const fixture = TestBed.createComponent(AiSnakeSharePanelComponent);
      fixture.componentInstance.view = view;
      fixture.detectChanges();

      await fixture.componentInstance.doEnd();
      fixture.changeDetectorRef.detectChanges();

      expect(fixture.componentInstance.view).toBe('home');
      expect(fixture.nativeElement.querySelector('[data-testid="pair-session-action-error"]')?.textContent)
        .toContain('membership_capability_retired');
      confirm.mockRestore();
    },
  );
});
