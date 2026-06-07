import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule, AsyncPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AiSnakeChatService } from '../services/ai-snake-chat.service';
import { AiSnakeConfigService } from '../services/ai-snake-config.service';
import { OidcAuthService } from '../services/oidc-auth.service';
import {
  PUBLIC_KEYCLOAK_BASE_URL,
  PUBLIC_OIDC_REALM,
  PUBLIC_WEBRTC_BASE_URL,
} from '../services/public-ananta-endpoints';
import { WebrtcSignalingService } from '../services/webrtc-signaling.service';
import { AiSnakeConfigPanelComponent } from './ai-snake-config-panel.component';
import { AiSnakeSharePanelComponent } from './ai-snake-share-panel.component';

@Component({
  selector: 'app-ai-snake-chat-panel',
  standalone: true,
  imports: [CommonModule, AsyncPipe, FormsModule, AiSnakeConfigPanelComponent, AiSnakeSharePanelComponent],
  template: `
    <div class="snake-chat-panel">
      <div class="head">
        <span>AI-Snake Chat</span>
        @if (svc.active$ | async) {
          <span class="badge">● verbunden</span>
        } @else {
          <span class="badge off">offline</span>
        }
      </div>

      @if (tab === 'settings') {
        <div class="settings-shell">
          <app-ai-snake-config-panel />
        </div>
      } @else if (tab === 'mode') {
        <div class="mode-shell">
          <div class="mode-group">
            <div class="title">Chat-Modus</div>
            <div class="mode-tabs">
              <button [class.active]="mode() === 'snake_ask'" (click)="setMode('snake_ask')">snake_ask</button>
              <button [class.active]="mode() === 'propose'" (click)="setMode('propose')">propose</button>
              <button [class.active]="mode() === 'auto'" (click)="setMode('auto')">auto</button>
            </div>
          </div>
          <div class="mode-group">
            <div class="title">Backend</div>
            <select [value]="backend()" (change)="setBackend($any($event.target).value)">
              <option value="ananta-worker">ananta-worker</option>
              <option value="opencode">opencode</option>
              <option value="lmstudio">lmstudio</option>
              <option value="hermes">hermes</option>
            </select>
          </div>
          <div class="mode-group">
            <label><input type="checkbox" [checked]="useCodeCompass()" (change)="setUseCodeCompass($any($event.target).checked)" /> CodeCompass nutzen</label>
          </div>
        </div>
      } @else if (tab === 'pair') {
        @if (oidc.loggedIn$ | async) {
          <div class="pair-header">
            <span class="pair-user">{{ oidc.currentUsername }}</span>
            <span class="pair-sig-status" [class.on]="(signaling.status$ | async) === 'connected'">
              WebRTC: {{ signaling.status$ | async }}
            </span>
          </div>
          <div class="settings-shell">
            <app-ai-snake-share-panel />
          </div>
        } @else {
          <div class="connect">
            <div class="muted">Pair Dev erfordert Keycloak-Login.</div>
            <button (click)="setTab('login')">Zum Login</button>
          </div>
        }
      } @else if (tab === 'deprecated') {
        <div class="mode-shell">
          <div class="mode-group">
            <div class="title">Deprecated Ansicht</div>
            <div class="muted">Altansicht bleibt verfuegbar. Bitte neue Tabs unten nutzen.</div>
          </div>
          <div class="mode-group">
            <button (click)="setTab('chat')">Zur neuen Chat-Ansicht</button>
            <button (click)="setTab('settings')">Zur neuen Einstellungen-Ansicht</button>
            <button (click)="setTab('pair')">Zu Pair Development</button>
          </div>
        </div>
      } @else if (tab === 'login') {
        <div class="connect">
          @if (oidc.loggedIn$ | async) {
            <div class="login-status ok">
              <span class="login-dot">●</span>
              <span>{{ oidc.currentUsername || 'Angemeldet' }}</span>
            </div>
            <div class="muted">Keycloak: {{ keycloakIssuer }}</div>
            <button class="ghost" (click)="keycloakLogout()">Abmelden</button>
            <hr class="divider" />
          } @else {
            <div class="login-status off">
              <span class="login-dot">○</span>
              <span>Nicht angemeldet</span>
            </div>
            <label>Keycloak URL
              <input [(ngModel)]="keycloakBaseUrl" [attr.list]="'snake-keycloak-presets'" (change)="onKeycloakUrlChange()" />
              <datalist id="snake-keycloak-presets">
                @for (p of keycloakPresets; track p) {
                  <option [value]="p">{{ p }}</option>
                }
              </datalist>
            </label>
            <label>Realm
              <input [(ngModel)]="keycloakRealm" (change)="onKeycloakUrlChange()" placeholder="ananta-e2e" />
            </label>
            <button (click)="keycloakLogin()" [disabled]="loginBusy">
              {{ loginBusy ? 'Öffne Login…' : 'Mit Keycloak anmelden' }}
            </button>
            @if (loginError) { <div class="error">{{ loginError }}</div> }
          }
          <hr class="divider" />
          <label>Name <input [(ngModel)]="name" /></label>
          <label>Rolle
            <select [(ngModel)]="role">
              <option value="viewer">viewer</option>
              <option value="player">player</option>
              <option value="coach">coach</option>
              <option value="tutor">tutor</option>
              <option value="critic">critic</option>
            </select>
          </label>
          <button (click)="connect()">Mit AI-Snake verbinden</button>
          <button class="ghost" (click)="disconnect()" [disabled]="(svc.active$ | async) !== true">Trennen</button>
        </div>
      } @else if ((svc.active$ | async) === false) {
        <div class="connect">
          <div class="muted">Nicht verbunden. Nutze den Tab "Login".</div>
          <button (click)="setTab('login')">Zum Login</button>
        </div>
      } @else {
        <div class="body">
          <div class="participants">
            <div class="title">Teilnehmer</div>
            @for (p of (svc.participants$ | async) || []; track p.id) {
              <div class="row">
                <span>{{ p.name }} ({{ p.role }})</span>
                <span class="status" [class.on]="p.status==='online'">{{ p.status }}</span>
              </div>
            }
          </div>
          <div class="messages">
            @for (m of (svc.messages$ | async) || []; track m.id) {
              <div class="msg">
                <strong>{{ m.sender_id }}:</strong> {{ m.text }}
              </div>
            }
          </div>
          <div class="send">
            <input [(ngModel)]="draft" (keydown.enter)="send()" placeholder="Nachricht an room..." [disabled]="!!(svc.awaitingReply$ | async)" />
            <button (click)="send()" [disabled]="!draft.trim() || !!(svc.awaitingReply$ | async)">Senden</button>
            @if (svc.awaitingReply$ | async) {
              <button class="cancel-btn" (click)="cancelChat()">⏹ Abbrechen</button>
            } @else {
              <button class="ghost" (click)="disconnect()">Trennen</button>
            }
          </div>
        </div>
      }
      <div class="bottom-tabs">
        <button [class.active]="tab==='chat'" (click)="setTab('chat')">Chat</button>
        <button [class.active]="tab==='login'" (click)="setTab('login')">AI-Snake</button>
        <button [class.active]="tab==='pair'" (click)="setTab('pair')">Pair Dev</button>
        <button [class.active]="tab==='mode'" (click)="setTab('mode')">Modus</button>
        <button [class.active]="tab==='settings'" (click)="setTab('settings')">Einstellungen</button>
      </div>
      @if (svc.error$ | async; as e) {
        @if (e) { <div class="error">{{ e }}</div> }
      }
    </div>
  `,
  styles: [`
    :host { height: 100%; display: block; }
    .snake-chat-panel { height: 100%; display: flex; flex-direction: column; background: #0b1220; color: #c8d8f8; min-height: 0; }
    .head { padding: 8px 10px; border-bottom: 1px solid #1a2d4a; background: #0d1828; display: flex; justify-content: space-between; }
    .badge { color: #7fffd4; font-size: 11px; } .badge.off { color: #6b8ab8; }
    .connect { padding: 10px; display: grid; gap: 8px; }
    .connect label { display: grid; gap: 4px; font-size: 11px; }
    input, select, button { background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8; padding: 5px 7px; font-family: inherit; font-size: 12px; }
    button { cursor: pointer; }
    .body { flex: 1; min-height: 0; display: grid; grid-template-rows: auto 1fr auto; }
    .mode-shell { flex: 1; min-height: 0; padding: 10px; display: grid; gap: 10px; align-content: start; }
    .mode-group { display: grid; gap: 6px; }
    .mode-tabs { display: flex; gap: 6px; }
    .mode-tabs button { border: 1px solid #1a2d4a; color: #6b8ab8; background: transparent; }
    .mode-tabs button.active { color: #7fffd4; border-color: #7fffd4; background: #102238; }
    .settings-shell { flex: 1; min-height: 0; overflow: hidden; }
    .participants { padding: 8px 10px; border-bottom: 1px solid #1a2d4a; max-height: 150px; overflow: auto; }
    .title { color: #6b8ab8; font-size: 11px; margin-bottom: 4px; }
    .row { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; }
    .status { color: #6b8ab8; } .status.on { color: #7fffd4; }
    .messages { padding: 8px 10px; overflow: auto; }
    .msg { margin-bottom: 5px; font-size: 12px; word-break: break-word; }
    .send { border-top: 1px solid #1a2d4a; padding: 8px 10px; display: grid; grid-template-columns: 1fr auto auto; gap: 6px; }
    .ghost { color: #6b8ab8; }
    .cancel-btn { color: #ff6b6b; border-color: #ff6b6b; background: #1a0a0a; }
    .divider { border: none; border-top: 1px solid #1a2d4a; margin: 6px 0; }
    .login-status { display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 2px 0; }
    .login-status.ok { color: #7fffd4; }
    .login-status.off { color: #4a6a9a; }
    .login-dot { font-size: 10px; }
    .pair-header { display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; border-bottom: 1px solid #1a2d4a; font-size: 11px; background: #0d1828; flex-shrink: 0; }
    .pair-user { color: #7fffd4; }
    .pair-sig-status { color: #4a6a9a; }
    .pair-sig-status.on { color: #7fffd4; }
    .bottom-tabs {
      margin-top: auto;
      border-top: 1px solid #1a2d4a;
      padding: 6px 8px;
      display: flex;
      justify-content: flex-end;
      gap: 6px;
      background: #0d1828;
      position: sticky;
      bottom: 0;
      z-index: 4;
    }
    .bottom-tabs button { border: 1px solid #1a2d4a; color: #6b8ab8; background: transparent; padding: 4px 9px; cursor: pointer; }
    .bottom-tabs button.active { color: #7fffd4; border-color: #2a4070; background: #102238; }
    .error { color: #fb7185; font-size: 11px; padding: 6px 10px; border-top: 1px solid #4a1a1a; }
  `],
})
export class AiSnakeChatPanelComponent {
  readonly svc = inject(AiSnakeChatService);
  readonly cfg = inject(AiSnakeConfigService);
  readonly oidc = inject(OidcAuthService);
  readonly signaling = inject(WebrtcSignalingService);

  name = 'web-ai-snake';
  role = 'viewer';
  draft = '';
  keycloakBaseUrl = PUBLIC_KEYCLOAK_BASE_URL;
  keycloakRealm = PUBLIC_OIDC_REALM;
  webrtcBaseUrl = PUBLIC_WEBRTC_BASE_URL;
  loginBusy = false;
  loginError = '';
  readonly keycloakPresets = [PUBLIC_KEYCLOAK_BASE_URL];

  @Input() tab: 'chat' | 'login' | 'pair' | 'mode' | 'settings' | 'deprecated' = 'chat';
  @Output() tabChange = new EventEmitter<'chat' | 'login' | 'pair' | 'mode' | 'settings' | 'deprecated'>();

  get keycloakIssuer(): string {
    return `${this.keycloakBaseUrl.replace(/\/$/, '')}/realms/${this.keycloakRealm || 'ananta-e2e'}`;
  }

  constructor() {
    this.cfg.load();
    this.restoreRuntimeEndpoints();
  }

  async keycloakLogin(): Promise<void> {
    this.loginError = '';
    this.loginBusy = true;
    try {
      await this.oidc.startLoginPopup(this.keycloakIssuer);
    } catch (e: any) {
      this.loginError = String(e?.message ?? 'Login fehlgeschlagen');
    } finally {
      this.loginBusy = false;
    }
  }

  async keycloakLogout(): Promise<void> {
    await this.oidc.logout();
  }

  connect(): void {
    void this.svc.connect(this.name.trim() || 'web-ai-snake', this.role);
  }

  disconnect(): void {
    this.svc.disconnect();
  }

  send(): void {
    const text = this.draft.trim();
    if (!text) return;
    this.svc.sendRoomMessage(text);
    this.draft = '';
  }

  cancelChat(): void {
    this.svc.cancelChat();
  }

  mode(): string {
    return String(this.cfg.config$.value['chat_worker_mode'] || 'snake_ask');
  }

  setMode(value: 'snake_ask' | 'propose' | 'auto'): void {
    this.cfg.updateField('chat_worker_mode', value);
  }

  backend(): string {
    return String(this.cfg.config$.value['chat_backend'] || 'ananta-worker');
  }

  setBackend(value: string): void {
    this.cfg.updateField('chat_backend', value);
  }

  useCodeCompass(): boolean {
    return !!this.cfg.config$.value['chat_use_codecompass'];
  }

  setUseCodeCompass(enabled: boolean): void {
    this.cfg.updateField('chat_use_codecompass', enabled);
  }

  setTab(tab: 'chat' | 'login' | 'pair' | 'mode' | 'settings' | 'deprecated'): void {
    this.tab = tab;
    this.tabChange.emit(tab);
  }

  onKeycloakUrlChange(): void {
    this.persistRuntimeEndpoints();
  }

  private restoreRuntimeEndpoints(): void {
    try {
      const raw = localStorage.getItem('ananta.ai-snake.runtime-endpoints.v1');
      if (!raw) return;
      const parsed = JSON.parse(raw);
      const keycloak = String(parsed?.keycloakBaseUrl || '').trim();
      const realm = String(parsed?.keycloakRealm || '').trim();
      const webrtc = String(parsed?.webrtcBaseUrl || '').trim();
      if (keycloak) this.keycloakBaseUrl = keycloak;
      if (realm) this.keycloakRealm = realm;
      if (webrtc) this.webrtcBaseUrl = webrtc;
    } catch {}
  }

  persistRuntimeEndpoints(): void {
    try {
      localStorage.setItem(
        'ananta.ai-snake.runtime-endpoints.v1',
        JSON.stringify({
          keycloakBaseUrl: this.keycloakBaseUrl.trim(),
          keycloakRealm: this.keycloakRealm.trim(),
          webrtcBaseUrl: this.webrtcBaseUrl.trim(),
        }),
      );
    } catch {}
  }
}
