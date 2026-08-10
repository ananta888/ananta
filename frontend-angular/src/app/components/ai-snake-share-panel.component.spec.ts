import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { AiSnakeSharePanelComponent } from './ai-snake-share-panel.component';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiCoreService } from '../services/hub-api-core.service';
import { PairViewSessionBindingService } from '../services/pair-view-session-binding.service';
import { ShareSessionService } from '../services/share-session.service';

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
    state$, securityState$, isActive: true, currentUserId: 'alice',
    canSendChat: () => securityState$.value.status === 'ready' || securityState$.value.status === 'legacy',
    sendMessage: vi.fn(async () => undefined), approveFingerprintChange: vi.fn(),
    participantStatus: () => 'online', endSession: vi.fn(), leaveSession: vi.fn(),
    revokeParticipant: vi.fn(), createSession: vi.fn(), joinSession: vi.fn(),
    discardPendingJoinAttempt: vi.fn(),
  };
  const binding = { start: vi.fn() };
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
      { provide: AgentDirectoryService, useValue: { list: () => [] } },
      { provide: HubApiCoreService, useValue: core },
    ],
  });
  return { service, binding, core };
}

describe('AiSnakeSharePanelComponent production security host', () => {
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
});
