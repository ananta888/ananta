import { Component, EventEmitter, Input, Output, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule, AsyncPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
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
import { AiSnakeTraceViewerComponent } from './ai-snake-trace-viewer.component';
import { ChatSessionsPanelComponent } from './chat-sessions-panel.component';
import { ChatSessionsService, ChatSession } from '../services/chat-sessions.service';
import { ChatHistoryService, ChatHistoryMessage } from '../services/chat-history.service';
import { ChatMessageComponent } from './chat-message.component';
import { UiStateSyncService } from '../services/ui-state-sync.service';

@Component({
  selector: 'app-ai-snake-chat-panel',
  standalone: true,
  imports: [CommonModule, AsyncPipe, FormsModule, AiSnakeConfigPanelComponent, AiSnakeSharePanelComponent, AiSnakeTraceViewerComponent, ChatSessionsPanelComponent, ChatMessageComponent],
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
      } @else if (tab === 'sessions') {
        <div class="settings-shell">
          <app-chat-sessions-panel />
        </div>
      } @else if (tab === 'trace') {
        <div class="trace-shell">
          <app-ai-snake-trace-viewer />
        </div>
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

          <!-- ── Chat-Auswahl: Combobox + Neu-Button ── -->
          <div class="chat-switcher">
            <select class="sess-select"
                    [ngModel]="sessions.activeSessionId$ | async"
                    (ngModelChange)="switchSession($event)">
              @for (grp of sessionGroups(); track grp.name) {
                @if (grp.name) {
                  <optgroup [label]="grp.name">
                    @for (s of grp.sessions; track s.id) {
                      <option [value]="s.id">{{ s.icon || '💬' }} {{ s.name }}</option>
                    }
                  </optgroup>
                } @else {
                  @for (s of grp.sessions; track s.id) {
                    <option [value]="s.id">{{ s.icon || '💬' }} {{ s.name }}</option>
                  }
                }
              }
            </select>
            <button class="new-chat-btn" (click)="newChatMode = !newChatMode" title="Neuen Chat anlegen">＋</button>
            <button class="sess-mgr-btn" (click)="setTab('sessions')" title="Chats verwalten">⚙</button>
          </div>

          <!-- ── Neuen Chat anlegen ── -->
          @if (newChatMode) {
            <div class="new-chat-form">
              <input [(ngModel)]="newChatName" placeholder="Chat-Name *" (keydown.enter)="createChat()" class="new-chat-input" />
              <button (click)="createChat()" [disabled]="!newChatName.trim()" class="new-chat-ok">Anlegen</button>
              <button (click)="newChatMode = false" class="ghost">✕</button>
            </div>
          }

          <!-- ── Aktive-Session-Info ── -->
          @if (sessions.activeSessionId$ | async; as activeId) {
            @if (activeSessionFor(activeId); as sess) {
              <div class="session-bar">
                <span class="sess-dot">●</span>
                <span class="sess-name-label">{{ sess.name }}</span>
                <span class="sess-meta">{{ sessBackend(sess) }}</span>
                @if (sessCodeCompass(sess)) { <span class="sess-cc">CC</span> }
                <span class="msg-count">{{ chatMessages().length }} Nachrichten</span>
              </div>
            }
          }

          <div class="messages" #messagesEl>
            @if (chatMessages().length === 0) {
              <div class="no-msgs-hint">
                Noch keine Nachrichten in diesem Chat.<br>
                Schreib etwas unten um zu starten.
              </div>
            }
            @for (m of chatMessages(); track m.id) {
              <div class="msg" [class.msg-ai]="m.isAI">
                <span class="msg-who">{{ m.isAI ? '🤖' : '👤' }}</span>
                <span class="msg-body">
                  <app-chat-message [text]="m.text" />
                </span>
              </div>
            }
            @if (svc.awaitingReply$ | async) {
              <div class="msg msg-ai typing">
                <span class="msg-who">🤖</span>
                <span class="msg-body">…</span>
              </div>
            }
          </div>
          <div class="send">
            <input [(ngModel)]="draft" (keydown.enter)="send()"
                   [placeholder]="sendPlaceholder()"
                   [disabled]="!!(svc.awaitingReply$ | async)" />
            <button (click)="send()" [disabled]="!draft.trim() || !!(svc.awaitingReply$ | async)">Senden</button>
            @if (svc.awaitingReply$ | async) {
              <button class="cancel-btn" (click)="cancelChat()">⏹</button>
            }
          </div>
        </div>
      }
      <div class="bottom-tabs">
        <button [class.active]="tab==='chat'" (click)="setTab('chat')" data-waypoint="snake.tab-chat">Chat</button>
        <button [class.active]="tab==='sessions'" (click)="setTab('sessions')" data-waypoint="snake.tab-sessions">Sessions</button>
        <button [class.active]="tab==='trace'" (click)="setTab('trace')" class="trace-tab-btn" data-waypoint="snake.tab-trace">Trace</button>
        <button [class.active]="tab==='login'" (click)="setTab('login')" data-waypoint="snake.tab-ai-snake">AI-Snake</button>
        <button [class.active]="tab==='pair'" (click)="setTab('pair')" data-waypoint="snake.tab-pair">Pair Dev</button>
        <button [class.active]="tab==='mode'" (click)="setTab('mode')" data-waypoint="snake.tab-mode">Modus</button>
        <button [class.active]="tab==='settings'" (click)="setTab('settings')" data-waypoint="snake.tab-settings">Einstellungen</button>
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
    .body { flex: 1; min-height: 0; display: flex; flex-direction: column; }
    .mode-shell { flex: 1; min-height: 0; padding: 10px; display: grid; gap: 10px; align-content: start; }
    .mode-group { display: grid; gap: 6px; }
    .mode-tabs { display: flex; gap: 6px; }
    .mode-tabs button { border: 1px solid #1a2d4a; color: #6b8ab8; background: transparent; }
    .mode-tabs button.active { color: #7fffd4; border-color: #7fffd4; background: #102238; }
    .settings-shell { flex: 1; min-height: 0; overflow: hidden; }
    .trace-shell { flex: 1; min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
    /* ── Chat-Switcher ── */
    .chat-switcher {
      display: flex; align-items: center; gap: 4px;
      padding: 5px 8px; background: #09172a; border-bottom: 1px solid #152040; flex-shrink: 0;
    }
    .sess-select {
      flex: 1; background: #0f1c30; border: 1px solid #1a3050; color: #c8d8f8;
      padding: 5px 7px; font-size: 12px; font-family: inherit; border-radius: 3px; cursor: pointer;
    }
    .new-chat-btn {
      background: #0a2238; border: 1px solid #2a5080; color: #7fffd4;
      padding: 3px 9px; cursor: pointer; font-size: 15px; border-radius: 3px;
    }
    .new-chat-btn:hover { background: #103050; }
    .sess-mgr-btn {
      background: transparent; border: 1px solid #1a3050; color: #4a6a9a;
      padding: 3px 7px; cursor: pointer; font-size: 13px; border-radius: 3px;
    }
    .sess-mgr-btn:hover { color: #7fffd4; }
    .new-chat-form {
      display: flex; align-items: center; gap: 5px;
      padding: 5px 8px; background: #08131f; border-bottom: 1px solid #152040; flex-shrink: 0;
    }
    .new-chat-input {
      flex: 1; background: #0f1c30; border: 1px solid #2a4070; color: #c8d8f8;
      padding: 4px 7px; font-size: 12px; font-family: inherit; border-radius: 3px;
    }
    .new-chat-ok {
      background: #102238; border: 1px solid #2a5090; color: #7fffd4;
      padding: 4px 9px; cursor: pointer; font-size: 12px; border-radius: 3px;
    }
    .new-chat-ok:disabled { opacity: 0.35; cursor: default; }
    /* ── Session-Info-Bar ── */
    .session-bar {
      display: flex; align-items: center; gap: 6px;
      padding: 3px 10px; background: #0d1e34; border-bottom: 1px solid #152040;
      font-size: 11px; flex-shrink: 0;
    }
    .sess-dot { color: #7fffd4; font-size: 7px; }
    .sess-name-label { color: #c8d8f8; font-weight: 500; }
    .sess-meta { color: #4a7aaa; }
    .sess-cc { background: #0a2a1a; border: 1px solid #1a6a3a; color: #3affaa; padding: 1px 4px; font-size: 9px; border-radius: 2px; }
    .msg-count { margin-left: auto; color: #2a4a6a; font-size: 10px; }
    /* ── Messages ── */
    .messages { padding: 8px 10px; overflow: auto; display: flex; flex-direction: column; gap: 6px; }
    .no-msgs-hint { color: #2a4a6a; font-size: 11px; text-align: center; padding: 20px 10px; line-height: 1.6; }
    .msg { display: flex; gap: 6px; font-size: 12px; word-break: break-word; align-items: flex-start; }
    .msg-ai .msg-body { color: #b8d8b0; }
    .msg-who { flex-shrink: 0; font-size: 13px; }
    .msg-body { flex: 1; min-width: 0; white-space: pre-wrap; }
    .typing .msg-body { color: #4a8a6a; animation: blink 1s infinite; }
    @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
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
    .trace-tab-btn { border-color: #1a3a2a; color: #3a8a6a; }
    .trace-tab-btn.active { color: #3accaa; border-color: #1a6a4a; background: #0a2018; }
    .error { color: #fb7185; font-size: 11px; padding: 6px 10px; border-top: 1px solid #4a1a1a; }
  `],
})
export class AiSnakeChatPanelComponent implements OnInit, OnDestroy {
  readonly svc = inject(AiSnakeChatService);
  readonly cfg = inject(AiSnakeConfigService);
  readonly oidc = inject(OidcAuthService);
  readonly signaling = inject(WebrtcSignalingService);
  readonly sessions = inject(ChatSessionsService);
  readonly history = inject(ChatHistoryService);
  private uiSync = inject(UiStateSyncService);

  name = 'web-ai-snake';
  role = 'viewer';
  draft = '';
  keycloakBaseUrl = PUBLIC_KEYCLOAK_BASE_URL;
  keycloakRealm = PUBLIC_OIDC_REALM;
  webrtcBaseUrl = PUBLIC_WEBRTC_BASE_URL;
  loginBusy = false;
  loginError = '';
  newChatMode = false;
  newChatName = '';
  readonly keycloakPresets = [PUBLIC_KEYCLOAK_BASE_URL];

  private historySub?: Subscription;
  private activeSub?: Subscription;

  @Input() tab: 'chat' | 'sessions' | 'trace' | 'login' | 'pair' | 'mode' | 'settings' | 'deprecated' = 'chat';
  @Output() tabChange = new EventEmitter<'chat' | 'sessions' | 'trace' | 'login' | 'pair' | 'mode' | 'settings' | 'deprecated'>();

  get keycloakIssuer(): string {
    return `${this.keycloakBaseUrl.replace(/\/$/, '')}/realms/${this.keycloakRealm || 'ananta-e2e'}`;
  }

  constructor() {
    this.cfg.load();
    this.restoreRuntimeEndpoints();
    this.sessions.load();
  }

  ngOnInit(): void {
    this.historySub = this.history.updated$.subscribe(() => {});
    this.activeSub = this.svc.active$.subscribe(active => {
      if (active) {
        this.uiSync.start();
      } else {
        this.uiSync.stop();
      }
    });
  }

  ngOnDestroy(): void {
    this.historySub?.unsubscribe();
    this.activeSub?.unsubscribe();
    this.uiSync.stop();
  }

  chatMessages(): ChatHistoryMessage[] {
    const sid = this.sessions.activeSessionId$.value || 'default';
    return this.history.getMessages(sid);
  }

  sendPlaceholder(): string {
    const sid = this.sessions.activeSessionId$.value;
    const sess = sid ? this.activeSessionFor(sid) : null;
    return sess ? `Nachricht in "${sess.name}"…` : 'Nachricht senden…';
  }

  createChat(): void {
    const name = this.newChatName.trim();
    if (!name) return;
    this.sessions.create({ name, icon: '💬', system_prompt: '', settings: {} });
    this.newChatName = '';
    this.newChatMode = false;
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
    this.uiSync.start();
  }

  disconnect(): void {
    this.uiSync.stop();
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

  activeSessionFor(id: string) {
    return (this.sessions.sessions$.value || []).find(s => s.id === id) ?? null;
  }

  sessBackend(s: { settings?: Record<string, unknown> }): string {
    return String(s.settings?.['chat_backend'] ?? '');
  }

  sessCodeCompass(s: { settings?: Record<string, unknown> }): boolean {
    return !!s.settings?.['chat_use_codecompass'];
  }

  switchSession(id: string): void {
    this.sessions.activate(id);
  }

  sessionGroups(): Array<{ name: string; sessions: ChatSession[] }> {
    const all = this.sessions.sessions$.value || [];
    const map = new Map<string, ChatSession[]>();
    for (const s of all) {
      const g = s.group || '';
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(s);
    }
    const result: Array<{ name: string; sessions: ChatSession[] }> = [];
    if (map.has('')) result.push({ name: '', sessions: map.get('')! });
    for (const [name, list] of [...map.entries()].filter(([k]) => k).sort((a, b) => a[0].localeCompare(b[0]))) {
      result.push({ name, sessions: list });
    }
    return result;
  }

  setTab(tab: 'chat' | 'sessions' | 'trace' | 'login' | 'pair' | 'mode' | 'settings' | 'deprecated'): void {
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
