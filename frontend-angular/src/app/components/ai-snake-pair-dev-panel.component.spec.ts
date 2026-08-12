import { Component, Input, ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { BehaviorSubject } from 'rxjs';

import { AiSnakePairDevPanelComponent } from './ai-snake-pair-dev-panel.component';
import { AiSnakeSharePanelComponent } from './ai-snake-share-panel.component';
import { SemanticMediaProgramHostComponent } from '../features/voice/semantic-media-program-host.component';
import { NetworkProfileService } from '../services/network-profile.service';
import { OidcAuthService } from '../services/oidc-auth.service';
import { PairPublicAuthorityPolicy } from '../services/pair-public-authority.policy';
import { UserAuthService } from '../services/user-auth.service';
import { WebrtcSignalingService } from '../services/webrtc-signaling.service';

@Component({ selector: 'app-ai-snake-share-panel', standalone: true, template: 'Pair session chat' })
class StubSharePanelComponent { @Input() publicOnly = false; }

@Component({
  selector: 'app-semantic-media-program-host',
  standalone: true,
  template: 'Pair media controls',
})
class StubMediaHostComponent { @Input() displayMode = ''; }

beforeAll(async () => {
  await ɵresolveComponentResources(resource => {
    const file = path.basename(String(resource));
    const directory = file.startsWith('pair-compute-') ? 'pair-view' : 'voice';
    return readFile(path.resolve(process.cwd(), 'src/app/features', directory, file), 'utf8');
  });
});

describe('AiSnakePairDevPanelComponent compact surfaces', () => {
  beforeEach(async () => {
    const profile$ = new BehaviorSubject({ profile_id: 'public-ananta' });
    const token$ = new BehaviorSubject<string | null>('oidc-token');
    await TestBed.configureTestingModule({
      imports: [AiSnakePairDevPanelComponent],
      providers: [
        { provide: NetworkProfileService, useValue: { profile$: profile$.asObservable() } },
        { provide: UserAuthService, useValue: { oidcToken$: token$.asObservable() } },
        { provide: PairPublicAuthorityPolicy, useValue: { ready: true } },
        {
          provide: OidcAuthService,
          useValue: { currentUsername: 'alice', startLoginPopup: vi.fn() },
        },
        {
          provide: WebrtcSignalingService,
          useValue: { status$: new BehaviorSubject('connected') },
        },
      ],
    })
      .overrideComponent(AiSnakePairDevPanelComponent, {
        remove: { imports: [AiSnakeSharePanelComponent, SemanticMediaProgramHostComponent] },
        add: { imports: [StubSharePanelComponent, StubMediaHostComponent] },
      })
      .compileComponents();
  });

  afterEach(() => TestBed.resetTestingModule());

  it('shows only session/chat by default and preserves one media owner across tab switches', () => {
    const fixture = TestBed.createComponent(AiSnakePairDevPanelComponent);
    fixture.detectChanges();
    const host = fixture.nativeElement as HTMLElement;
    const sessionSurface = host.querySelector<HTMLElement>('[data-testid="pair-session-chat-surface"]');
    const mediaSurface = host.querySelector<HTMLElement>('[data-testid="pair-media-runtime-owner"]');
    const mediaOwner = host.querySelector('app-semantic-media-program-host');

    expect(sessionSurface?.hidden).toBe(false);
    expect(mediaSurface?.hidden).toBe(true);
    expect(host.textContent).toContain('Pair session chat');

    fixture.componentRef.setInput('surface', 'media');
    fixture.detectChanges();

    expect(sessionSurface?.hidden).toBe(true);
    expect(mediaSurface?.hidden).toBe(false);
    expect(host.querySelector('app-semantic-media-program-host')).toBe(mediaOwner);
  });
});
