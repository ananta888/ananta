import { AsyncPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';

import { AiSnakeSharePanelComponent } from '../../components/ai-snake-share-panel.component';
import { SemanticMediaProgramHostComponent } from '../voice/semantic-media-program-host.component';
import { OidcAuthService } from '../../services/oidc-auth.service';
import { WebrtcSignalingService } from '../../services/webrtc-signaling.service';

@Component({
  selector: 'app-public-pair-page',
  standalone: true,
  imports: [AsyncPipe, AiSnakeSharePanelComponent, SemanticMediaProgramHostComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="public-pair-page" data-testid="public-pair-page" aria-labelledby="public-pair-title">
      <header class="public-pair-header">
        <div>
          <h1 id="public-pair-title">Public Pair Dev</h1>
          <p>Direkte, Ende-zu-Ende-verschlüsselte Pair-Session</p>
        </div>
        <div class="public-pair-status" aria-label="Public-Pair-Verbindungsstatus">
          <span data-testid="public-pair-user">{{ oidc.currentUsername || 'Angemeldet' }}</span>
          <span data-testid="public-pair-webrtc-status">WebRTC: {{ signaling.status$ | async }}</span>
        </div>
      </header>

      <section class="public-pair-session" aria-label="Session Sharing">
        <app-ai-snake-share-panel [publicOnly]="true" />
      </section>
      <section class="public-pair-media" aria-label="Audio und Video">
        <app-semantic-media-program-host displayMode="pair_media" />
      </section>
    </section>
  `,
  styles: [`
    :host { display: block; min-height: 100%; }
    .public-pair-page {
      box-sizing: border-box; max-width: 1080px; margin: 0 auto; padding: 20px;
      color: var(--fg); font-family: ui-monospace, Menlo, Consolas, monospace;
    }
    .public-pair-header {
      display: flex; align-items: flex-start; justify-content: space-between; gap: 20px;
      margin-bottom: 18px; padding: 16px; border: 1px solid var(--border);
      border-radius: 8px; background: var(--card-bg);
    }
    h1 { margin: 0 0 4px; font-size: 20px; }
    p { margin: 0; color: var(--muted); font-size: 12px; }
    .public-pair-status { display: grid; gap: 4px; text-align: right; color: var(--muted); font-size: 12px; }
    .public-pair-session, .public-pair-media {
      margin-top: 14px; padding: 12px; border: 1px solid var(--border);
      border-radius: 8px; background: var(--card-bg);
    }
    @media (max-width: 640px) {
      .public-pair-page { padding: 10px; }
      .public-pair-header { flex-direction: column; }
      .public-pair-status { text-align: left; }
    }
  `],
})
export class PublicPairPageComponent {
  readonly oidc = inject(OidcAuthService);
  readonly signaling = inject(WebrtcSignalingService);
}
