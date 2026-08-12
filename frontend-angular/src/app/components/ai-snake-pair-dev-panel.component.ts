import { AsyncPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input, inject } from '@angular/core';
import { combineLatest, distinctUntilChanged, map } from 'rxjs';

import { SemanticMediaProgramHostComponent } from '../features/voice/semantic-media-program-host.component';
import { NetworkProfileService } from '../services/network-profile.service';
import { OidcAuthService } from '../services/oidc-auth.service';
import { PairPublicAuthorityPolicy } from '../services/pair-public-authority.policy';
import { UserAuthService } from '../services/user-auth.service';
import { WebrtcSignalingService } from '../services/webrtc-signaling.service';
import { AiSnakeSharePanelComponent } from './ai-snake-share-panel.component';

@Component({
  selector: 'app-ai-snake-pair-dev-panel',
  standalone: true,
  imports: [AsyncPipe, AiSnakeSharePanelComponent, SemanticMediaProgramHostComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (authorityReady$ | async) {
      <div class="pair-header">
        <span class="pair-user">{{ oidc.currentUsername }}</span>
        <span class="pair-sig-status" [class.on]="(signaling.status$ | async) === 'connected'">
          WebRTC: {{ signaling.status$ | async }}
        </span>
      </div>
      <div
        class="pair-content"
        data-testid="pair-session-chat-surface"
        [hidden]="surface !== 'session'"
        [attr.inert]="surface !== 'session' ? '' : null"
        [attr.aria-hidden]="surface !== 'session'">
        <app-ai-snake-share-panel [publicOnly]="true" />
      </div>
      <div
        class="pair-media-surface"
        data-testid="pair-media-runtime-owner"
        [hidden]="surface !== 'media'"
        [attr.inert]="surface !== 'media' ? '' : null"
        [attr.aria-hidden]="surface !== 'media'">
        <!-- This host stays mounted across Pair/Media/Chat tabs so visual
             navigation cannot end the session-scoped media runtime. -->
        <app-semantic-media-program-host displayMode="pair_media" />
      </div>
    } @else {
      <div class="connect">
        <div class="muted">
          Pair Dev erfordert das bestätigte öffentliche Netzwerkprofil und Keycloak-Login.
        </div>
        <button type="button" (click)="login()" [disabled]="loginBusy">
          {{ loginBusy ? 'Öffne Login…' : 'Mit Keycloak anmelden' }}
        </button>
        @if (loginError) {
          <div class="error" role="alert">{{ loginError }}</div>
        }
      </div>
    }
  `,
  styles: [`
    :host { display: flex; flex: 1; min-height: 0; flex-direction: column; }
    .pair-header {
      display: flex; justify-content: space-between; align-items: center;
      padding: 6px 10px; border-bottom: 1px solid #1a2d4a;
      font-size: 11px; background: #0d1828; flex-shrink: 0;
    }
    .pair-user { color: #7fffd4; }
    .pair-sig-status { color: #4a6a9a; }
    .pair-sig-status.on { color: #7fffd4; }
    .pair-content, .pair-media-surface { flex: 1; min-height: 0; overflow: auto; }
    .pair-content[hidden], .pair-media-surface[hidden] { display: none !important; }
    .connect { display: grid; gap: 10px; padding: 16px; }
    .muted { color: #6b8ab8; }
    .error { color: #ff9f9f; }
    button {
      justify-self: start; background: #0f1c30; border: 1px solid #1a2d4a;
      color: #c8d8f8; padding: 5px 7px; font: inherit; cursor: pointer;
    }
  `],
})
export class AiSnakePairDevPanelComponent {
  @Input() surface: 'session' | 'media' = 'session';
  readonly oidc = inject(OidcAuthService);
  readonly publicAuthority = inject(PairPublicAuthorityPolicy);
  readonly signaling = inject(WebrtcSignalingService);
  private readonly profiles = inject(NetworkProfileService);
  private readonly userAuth = inject(UserAuthService);
  readonly authorityReady$ = combineLatest([
    this.userAuth.oidcToken$,
    this.profiles.profile$,
  ]).pipe(
    map(() => this.publicAuthority.ready),
    distinctUntilChanged(),
  );
  loginBusy = false;
  loginError = '';

  async login(): Promise<void> {
    this.loginError = '';
    this.loginBusy = true;
    try {
      await this.oidc.startLoginPopup();
    } catch (error: unknown) {
      this.loginError = error instanceof Error ? error.message : 'Login fehlgeschlagen';
    } finally {
      this.loginBusy = false;
    }
  }
}
