import { Component, Input } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { BehaviorSubject, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { AiSnakeSharePanelComponent } from '../../components/ai-snake-share-panel.component';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { OidcAuthService } from '../../services/oidc-auth.service';
import { PairViewSessionBindingService } from '../../services/pair-view-session-binding.service';
import { ShareSessionService } from '../../services/share-session.service';
import { WebrtcSignalingService } from '../../services/webrtc-signaling.service';
import { WebrtcSessionService } from '../../services/webrtc-session.service';
import { SemanticMediaProgramHostComponent } from '../voice/semantic-media-program-host.component';
import { PublicPairPageComponent } from './public-pair-page.component';

@Component({
  selector: 'app-semantic-media-program-host',
  standalone: true,
  template: '',
})
class SemanticMediaProgramHostStubComponent {
  @Input() displayMode = '';
}

describe('PublicPairPageComponent', () => {
  it('renders the direct Pair surface without initializing or exposing Hub group APIs', async () => {
    const signalingStatus$ = new BehaviorSubject('disconnected');
    const webrtcStatus$ = new BehaviorSubject('idle');
    const dataChannelStatus$ = new BehaviorSubject('absent');
    const core = {
      get: vi.fn(() => of({})),
      post: vi.fn(() => of({})),
      delete: vi.fn(() => of({})),
    };
    const share = {
      isActive: false,
      state$: new BehaviorSubject(null),
      securityState$: new BehaviorSubject({ status: 'idle' }),
      createSession: vi.fn(),
      joinSession: vi.fn(),
    };

    await TestBed.configureTestingModule({
      providers: [
        { provide: OidcAuthService, useValue: { currentUsername: 'keycloak-user' } },
        { provide: WebrtcSignalingService, useValue: { status$: signalingStatus$ } },
        {
          provide: WebrtcSessionService,
          useValue: { state$: webrtcStatus$, dataChannelState$: dataChannelStatus$ },
        },
        { provide: ShareSessionService, useValue: share },
        { provide: PairViewSessionBindingService, useValue: { start: vi.fn() } },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ role: 'hub', url: 'https://hub.example.test' }] },
        },
        { provide: HubApiCoreService, useValue: core },
      ],
    });
    TestBed.overrideComponent(PublicPairPageComponent, {
      remove: { imports: [SemanticMediaProgramHostComponent] },
      add: { imports: [SemanticMediaProgramHostStubComponent] },
    });
    await TestBed.compileComponents();

    const fixture = TestBed.createComponent(PublicPairPageComponent);
    fixture.detectChanges();
    const host = fixture.nativeElement as HTMLElement;
    const panel = fixture.debugElement.query(By.directive(AiSnakeSharePanelComponent))
      .componentInstance as AiSnakeSharePanelComponent;
    const media = fixture.debugElement.query(By.directive(SemanticMediaProgramHostStubComponent))
      .componentInstance as SemanticMediaProgramHostStubComponent;

    expect(host.querySelector('[data-testid="public-pair-page"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="public-pair-user"]')?.textContent).toContain('keycloak-user');
    expect(host.querySelector('[data-testid="public-pair-signaling-status"]')?.textContent)
      .toContain('Signaling: disconnected');
    expect(host.querySelector('[data-testid="public-pair-webrtc-status"]')?.textContent)
      .toContain('WebRTC: idle');
    expect(host.querySelector('[data-testid="public-pair-datachannel-status"]')?.textContent)
      .toContain('DataChannel: absent');
    expect(panel.publicOnly).toBe(true);
    expect(media.displayMode).toBe('pair_media');
    expect(host.textContent).not.toContain('Gruppen');
    expect(core.get).not.toHaveBeenCalled();
    expect(core.post).not.toHaveBeenCalled();
    expect(core.delete).not.toHaveBeenCalled();

    panel.switchToGroups();
    panel.startCreateGroup();
    panel.newGroupName = 'blocked';
    panel.doCreateGroup();
    panel.openGroup({ id: 'group-a' } as never);
    panel.addMember();
    panel.removeMember({ id: 'member-a' } as never);
    panel.deleteGroup({ id: 'group-a', name: 'blocked' } as never);
    panel.createGroupSession({ id: 'group-a' } as never);

    expect(core.get).not.toHaveBeenCalled();
    expect(core.post).not.toHaveBeenCalled();
    expect(core.delete).not.toHaveBeenCalled();
  });
});
