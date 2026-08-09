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
});
