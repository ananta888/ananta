import { Component, EventEmitter, Input, Output, inject, OnInit, OnDestroy, effect } from '@angular/core';
import { CommonModule, AsyncPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { AiSnakeChatService } from '../services/ai-snake-chat.service';
import { OidcAuthService } from '../services/oidc-auth.service';
import { IdentityBridge } from '../services/identity/identity-bridge';
import {
  PUBLIC_WEBRTC_BASE_URL,
} from '../services/public-ananta-endpoints';
import { AiSnakeConfigPanelComponent } from './ai-snake-config-panel.component';
import { AiSnakeTraceViewerComponent } from './ai-snake-trace-viewer.component';
import { ChatSessionsPanelComponent } from './chat-sessions-panel.component';
import { ChatSessionsService, ChatSession } from '../services/chat-sessions.service';
import { ChatHistoryService, ChatHistoryMessage } from '../services/chat-history.service';
import { ChatMessageComponent } from './chat-message.component';
import { UiStateSyncService } from '../services/ui-state-sync.service';
import { VisualSnakeLogComponent } from './visual-snake-log.component';
import { SnakeOverlayService } from '../services/snake-overlay.service';
import { AiSnakeProcessPanelComponent } from './ai-snake-process-panel.component';
import { VpNavigationService } from '../features/visual-process/vp-navigation.service';
import { AiSnakePanelTab } from './ai-snake-panel-tab';

@Component({
  selector: 'app-ai-snake-chat-panel',
  standalone: true,
  imports: [CommonModule, AsyncPipe, FormsModule, AiSnakeConfigPanelComponent, AiSnakeTraceViewerComponent, ChatSessionsPanelComponent, ChatMessageComponent, VisualSnakeLogComponent, AiSnakeProcessPanelComponent],
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
      } @else if (tab === 'sessions') {
        <div class="settings-shell">
          <app-chat-sessions-panel />
        </div>
      } @else if (tab === 'trace') {
        <div class="trace-shell">
          <app-ai-snake-trace-viewer />
        </div>
      } @else if (tab === 'process') {
        <app-ai-snake-process-panel />
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
            <label>OIDC-Issuer (Netzwerkprofil)
              <input [value]="keycloakIssuer" readonly aria-readonly="true" />
            </label>
            <div class="muted">Der Hub bzw. das aktive Netzwerkprofil legt Keycloak-Realm und Client fest.</div>
            <button (click)="keycloakLogin()" [disabled]="loginBusy">
              {{ loginBusy ? 'Öffne Login…' : 'Mit Keycloak anmelden' }}
            </button>
            @if (showRegistration) {
              <button
                type="button"
                class="ghost"
                (click)="registerWithKeycloak()"
                [disabled]="loginBusy"
                aria-label="Neues Konto bei Keycloak anlegen">
                Neues Konto bei Keycloak anlegen
              </button>
            }
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
                    [ngModel]="_snakeSessionId"
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
              <select [(ngModel)]="newChatProfileId" title="Chat-Profil">
                @for (profile of sessions.profiles$ | async; track profile.id) {
                  <option [value]="profile.id">{{ profile.icon || '🎯' }} {{ profile.name }}</option>
                }
              </select>
              <button (click)="createChat()" [disabled]="!newChatName.trim()" class="new-chat-ok">Anlegen</button>
              <button (click)="newChatMode = false" class="ghost">✕</button>
            </div>
          }

          <!-- ── Aktive-Session-Info ── -->
          @if (activeSessionFor(_snakeSessionId); as sess) {
            <div class="session-bar">
              <span class="sess-dot">●</span>
              <span class="sess-name-label">{{ sess.name }}</span>
              <span class="sess-meta">{{ sessBackend(sess) }}</span>
              @if (sessCodeCompass(sess)) { <span class="sess-cc">CC</span> }
              <span class="msg-count">{{ chatMessages().length }} Nachrichten</span>
            </div>
          }

          @if (!isVisualLogSession()) {
            <div class="summarize-bar">
              <button class="sum-toggle" [class.active]="selectMode" (click)="toggleSelectMode()">
                {{ selectMode ? '✕ Auswahl beenden' : '✂ Zusammenfassen' }}
              </button>
              @if (selectMode) {
                <span class="selection-help">Nachrichten anklicken, dann hier starten:</span>
                <select [(ngModel)]="summaryLength" class="sum-len">
                  <option [ngValue]="400">kurz (400)</option>
                  <option [ngValue]="800">mittel (800)</option>
                  <option [ngValue]="1500">lang (1500)</option>
                </select>
                <button class="sum-go" [disabled]="selectedMsgIds.size < 2 || summarizing"
                        (click)="summarizeSelected()">
                  {{ summarizing ? '⟳ Fasse zusammen…' : '▶ Jetzt zusammenfassen (' + selectedMsgIds.size + ')' }}
                </button>
              }
              @if (summaryHint) {
                <span class="sum-hint">{{ summaryHint }}</span>
              }
            </div>
            <div class="context-bar">
              <span>Kontext beim Fortsetzen: <strong>{{ contextMessageCount() }}</strong> ausgewählt (gesendet werden die letzten 20)</span>
              <span class="context-help">Mit ◉/○ pro Nachricht steuerbar</span>
            </div>
          }

          <div class="messages" #messagesEl>
            @if (isVisualLogSession()) {
              <app-visual-snake-log />
            } @else {
              @if (chatMessages().length === 0) {
                <div class="no-msgs-hint">
                  Noch keine Nachrichten in diesem Chat.<br>
                  Schreib etwas unten um zu starten.
                </div>
              }
              @for (m of chatMessages(); track m.id) {
                <div class="msg-row"
                     [class.context-excluded]="m.includedInContext === false"
                     [class.selectable]="selectMode"
                     [class.selected]="selectedMsgIds.has(m.id)"
                     (click)="onMsgRowClick(m.id)">
                  @if (selectMode) {
                    <input type="checkbox" class="sel-box"
                           [checked]="selectedMsgIds.has(m.id)"
                           (click)="$event.stopPropagation(); toggleMsgSelection(m.id)" />
                  }
                  @if (m.kind === 'summary') {
                    <div class="summary-box">
                      <div class="summary-head">📋 Zusammenfassung ({{ m.summarizedIds?.length || 0 }} Nachrichten)</div>
                      <div class="summary-text">{{ m.text }}</div>
                      @if (editingMessageId === m.id) {
                        <textarea class="message-editor" [(ngModel)]="editingMessageText" (click)="$event.stopPropagation()"></textarea>
                      }
                      <div class="summary-actions">
                        <button class="sum-mini"
                                (click)="$event.stopPropagation(); toggleExpandSummary(m.id)">
                          {{ expandedSummaryIds.has(m.id) ? '▾ Original verbergen' : '▸ Original anzeigen' }}
                        </button>
                        <button class="sum-mini"
                                (click)="$event.stopPropagation(); dissolveSummary(m.id)">↩ Auflösen</button>
                        <button class="sum-mini" (click)="$event.stopPropagation(); toggleContext(m)">
                          {{ m.includedInContext === false ? '○ Kontext aus' : '◉ Im Kontext' }}
                        </button>
                        <button class="sum-mini" (click)="$event.stopPropagation(); beginEdit(m)">✎ Bearbeiten</button>
                        <button class="sum-mini danger" (click)="$event.stopPropagation(); deleteMessage(m)">⌫ Löschen</button>
                      </div>
                      @if (editingMessageId === m.id) {
                        <div class="edit-actions">
                          <button class="sum-mini" (click)="$event.stopPropagation(); saveEdit(m)">Speichern</button>
                          <button class="sum-mini" (click)="$event.stopPropagation(); cancelEdit()">Abbrechen</button>
                        </div>
                      }
                      @if (expandedSummaryIds.has(m.id)) {
                        <div class="summary-originals">
                          @for (o of summarizedMessages(m.id); track o.id) {
                            <div class="msg orig-msg" [class.msg-ai]="o.isAI">
                              <span class="msg-who">{{ o.isAI ? '🤖' : '👤' }}</span>
                              <span class="msg-body">
                                <app-chat-message [text]="o.text" />
                              </span>
                            </div>
                          }
                        </div>
                      }
                    </div>
                  } @else {
                    <div class="msg" [class.msg-ai]="m.isAI">
                      <span class="msg-who">{{ m.isAI ? '🤖' : '👤' }}</span>
                      <span class="msg-body">
                        @if (editingMessageId === m.id) {
                          <textarea class="message-editor" [(ngModel)]="editingMessageText" (click)="$event.stopPropagation()"></textarea>
                          <span class="edit-actions">
                            <button class="sum-mini" (click)="$event.stopPropagation(); saveEdit(m)">Speichern</button>
                            <button class="sum-mini" (click)="$event.stopPropagation(); cancelEdit()">Abbrechen</button>
                          </span>
                        } @else {
                          <app-chat-message [text]="m.text" />
                        }
                      </span>
                      @if (m.hasGuide) {
                        <button class="guide-badge" (click)="setTab('trace')" title="Guide gespielt — Trace ansehen">📍</button>
                      }
                      <span class="message-actions">
                        <button class="sum-mini" (click)="$event.stopPropagation(); toggleContext(m)"
                                [title]="m.includedInContext === false ? 'Beim Fortsetzen wieder mitsenden' : 'Beim Fortsetzen nicht mitsenden'">
                          {{ m.includedInContext === false ? '○' : '◉' }}
                        </button>
                        <button class="sum-mini" (click)="$event.stopPropagation(); beginEdit(m)" title="Nachricht bearbeiten">✎</button>
                        @if (m.isAI) {
                          <button class="sum-mini" (click)="$event.stopPropagation(); regenerateMessage(m)"
                                  [disabled]="svc.awaitingReply$ | async"
                                  title="Nur diese Antwort neu generieren">↻</button>
                        }
                        <button class="sum-mini danger" (click)="$event.stopPropagation(); deleteMessage(m)" title="Nachricht löschen">⌫</button>
                      </span>
                    </div>
                  }
                </div>
              }
            }
            @if (svc.awaitingReply$ | async) {
              <div class="msg msg-ai typing">
                <span class="msg-who">🤖</span>
                <span class="msg-body">…</span>
              </div>
            }
          </div>
          <div class="send">
            @if (isActiveSessionReadOnly()) {
              <div class="read-only-banner" data-waypoint="chat.read-only-banner">
                🐍 Read-only Log-Session — wird ausschließlich vom Backend befüllt
                (UI-Ticks &amp; Antworten der visuellen Guide-Snake).
              </div>
            }
            <input [(ngModel)]="draft" (keydown.enter)="send()"
                   [placeholder]="sendPlaceholder()"
                   [disabled]="!!(svc.awaitingReply$ | async) || isActiveSessionReadOnly()" />
            <button (click)="send()" [disabled]="!draft.trim() || !!(svc.awaitingReply$ | async) || isActiveSessionReadOnly()">Senden</button>
            @if (svc.awaitingReply$ | async) {
              <button class="cancel-btn" (click)="cancelChat()">⏹</button>
            }
          </div>
        </div>
      }
      <div class="bottom-tabs">
        <button [class.active]="tab==='chat'" (click)="setTab('chat')" data-waypoint="snake.tab-chat">Chat</button>
        <button [class.active]="tab==='sessions'" (click)="setTab('sessions')" data-waypoint="snake.tab-sessions">Sessions</button>
        <button [class.active]="tab==='process'" (click)="setTab('process')" data-waypoint="snake.tab-process">Prozess</button>
        <button [class.active]="tab==='trace'" (click)="setTab('trace')" class="trace-tab-btn" data-waypoint="snake.tab-trace">Trace</button>
        <button [class.active]="tab==='login'" (click)="setTab('login')" data-waypoint="snake.tab-ai-snake">AI-Snake</button>
        <button type="button" (click)="openPairDev()" data-waypoint="snake.tab-pair" aria-label="Pair Dev in der Hauptansicht öffnen">Pair Dev</button>
        <button [class.active]="tab==='settings'" (click)="setTab('settings')" data-waypoint="snake.tab-settings">Einstell.</button>
        <button class="explain-btn"
                [class.active]="(overlayService.regionMode$ | async)"
                (click)="overlayService.toggleRegionMode()"
                title="Bereich aufziehen — Snake erklärt Elemente im Bereich"
                data-waypoint="snake.tab-explain">🔲 Erklären</button>
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
    /* ── Zusammenfassen-Leiste ── */
    .summarize-bar {
      display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
      padding: 4px 10px; background: #09172a; border-bottom: 1px solid #152040;
      font-size: 10px; flex-shrink: 0;
    }
    .sum-toggle {
      background: transparent; border: 1px solid #1a2d4a; color: #6b8ab8;
      padding: 2px 7px; font-size: 10px; cursor: pointer; border-radius: 3px;
    }
    .sum-toggle:hover { color: #7fffd4; }
    .sum-toggle.active { color: #7fffd4; border-color: #7fffd4; background: #0a2a1a; }
    .sum-len {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      padding: 2px 4px; font-size: 10px; border-radius: 3px;
    }
    .sum-go {
      background: #0a2a1a; border: 1px solid #2a5a4a; color: #7fffd4;
      padding: 2px 8px; font-size: 10px; cursor: pointer; border-radius: 3px;
    }
    .sum-go:disabled { opacity: 0.4; cursor: default; }
    .sum-hint { color: #d8c8a8; font-size: 10px; }
    .selection-help, .context-help { color: #6b8ab8; }
    .context-bar { display: flex; justify-content: space-between; gap: 8px; padding: 4px 10px; background: #0b1828; border-bottom: 1px solid #152040; font-size: 10px; flex-shrink: 0; }
    /* ── Messages ── */
    .messages { padding: 8px 10px; overflow: auto; display: flex; flex-direction: column; gap: 6px; }
    .msg-row { display: flex; gap: 6px; align-items: flex-start; }
    .msg-row.selectable { cursor: pointer; border-radius: 3px; padding: 1px 2px; }
    .msg-row.selectable:hover { background: #0d1e34; }
    .msg-row.selected { background: #0a2438; outline: 1px solid #2a5a4a; }
    .msg-row.context-excluded { opacity: 0.48; border-left: 2px solid #7a5360; padding-left: 4px; }
    .msg-row > .msg, .msg-row > .summary-box { flex: 1; min-width: 0; }
    .sel-box { flex-shrink: 0; margin-top: 3px; accent-color: #7fffd4; }
    /* ── Zusammenfassungs-Nachricht ── */
    .summary-box {
      background: #0a1a2a; border: 1px solid #2a5a4a; border-radius: 4px;
      padding: 6px 8px; font-size: 11px; display: grid; gap: 4px;
    }
    .summary-head { color: #7fffd4; font-size: 10px; font-weight: 600; }
    .summary-text { color: #c8d8f8; white-space: pre-wrap; word-break: break-word; }
    .summary-actions, .edit-actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .sum-mini {
      background: transparent; border: 1px solid #1a2d4a; color: #6b8ab8;
      padding: 1px 6px; font-size: 10px; cursor: pointer; border-radius: 3px;
    }
    .sum-mini:hover { color: #7fffd4; border-color: #2a5a4a; }
    .sum-mini.danger:hover { color: #fb7185; border-color: #fb7185; }
    .summary-originals {
      border-top: 1px dashed #1a2d4a; padding-top: 4px; margin-top: 2px;
      display: flex; flex-direction: column; gap: 4px; opacity: 0.6;
    }
    .orig-msg { font-size: 11px; }
    .no-msgs-hint { color: #2a4a6a; font-size: 11px; text-align: center; padding: 20px 10px; line-height: 1.6; }
    .msg { display: flex; gap: 6px; font-size: 12px; word-break: break-word; align-items: flex-start; }
    .msg-ai .msg-body { color: #b8d8b0; }
    .msg-who { flex-shrink: 0; font-size: 13px; }
    .msg-body { flex: 1; min-width: 0; white-space: pre-wrap; }
    .message-actions { display: flex; gap: 3px; opacity: 0.35; }
    .msg-row:hover .message-actions { opacity: 1; }
    .message-editor { width: 100%; min-height: 64px; resize: vertical; box-sizing: border-box; color: #dbe8ff; background: #07111f; border: 1px solid #3a5a7a; }
    .guide-badge { background: none; border: none; cursor: pointer; font-size: 12px; padding: 0 2px; opacity: 0.55; flex-shrink: 0; line-height: 1; }
    .guide-badge:hover { opacity: 1; }
    .typing .msg-body { color: #4a8a6a; animation: blink 1s infinite; }
    @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
    .send { border-top: 1px solid #1a2d4a; padding: 8px 10px; display: grid; grid-template-columns: 1fr auto auto; gap: 6px; }
    .read-only-banner { grid-column: 1 / -1; background: #1a2540; border: 1px solid #3a5a8a; color: #d8c8a8; padding: 6px 10px; border-radius: 3px; font-size: 11px; line-height: 1.4; }
    .ghost { color: #6b8ab8; }
    .cancel-btn { color: #ff6b6b; border-color: #ff6b6b; background: #1a0a0a; }
    .divider { border: none; border-top: 1px solid #1a2d4a; margin: 6px 0; }
    .login-status { display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 2px 0; }
    .login-status.ok { color: #7fffd4; }
    .login-status.off { color: #4a6a9a; }
    .login-dot { font-size: 10px; }
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
    .explain-btn { border-color: #2a4a2a; color: #4a9a6a; margin-left: auto; }
    .explain-btn:hover { color: #7fffd4; border-color: #3a7a4a; }
    .explain-btn.active { color: #7fffd4; border-color: #7fffd4; background: #0a2a1a; animation: pulse-exp 1s ease-in-out infinite; }
    @keyframes pulse-exp { 0%,100% { box-shadow: 0 0 0 0 rgba(127,255,212,0.5); } 50% { box-shadow: 0 0 0 5px rgba(127,255,212,0); } }
    .error { color: #fb7185; font-size: 11px; padding: 6px 10px; border-top: 1px solid #4a1a1a; }
  `],
})
export class AiSnakeChatPanelComponent implements OnInit, OnDestroy {
  readonly svc = inject(AiSnakeChatService);
  readonly oidc = inject(OidcAuthService);
  private readonly bridge = inject(IdentityBridge);
  readonly sessions = inject(ChatSessionsService);
  readonly history = inject(ChatHistoryService);
  readonly overlayService = inject(SnakeOverlayService);
  private uiSync = inject(UiStateSyncService);

  name = 'web-ai-snake';
  role = 'viewer';
  draft = '';
  webrtcBaseUrl = PUBLIC_WEBRTC_BASE_URL;
  loginBusy = false;
  loginError = '';
  newChatMode = false;
  newChatName = '';
  newChatProfileId = 'general';
  selectMode = false;
  selectedMsgIds = new Set<string>();
  summaryLength = 800;
  summarizing = false;
  summaryHint = '';
  expandedSummaryIds = new Set<string>();
  editingMessageId = '';
  editingMessageText = '';
  private summaryHintTimer: ReturnType<typeof setTimeout> | undefined;
  private historySub?: Subscription;
  private activeSub?: Subscription;
  private sessionSub?: Subscription;
  private readonly processNavigation=inject(VpNavigationService);
  /** Snake panel tracks its own session independently of the global ChatSessionsService.
   *  Persisted in localStorage so reconnects keep the same session. Defaults to 'ananta-settings'. */
  private _snakeSessionId: string = this.loadSnakeSession();

  @Input() tab: AiSnakePanelTab = 'chat';
  @Output() tabChange = new EventEmitter<AiSnakePanelTab>();
  @Output() pairDevRequested = new EventEmitter<void>();

  get keycloakIssuer(): string {
    return this.oidc.issuer;
  }

  constructor() {
    effect(()=>{if(this.processNavigation.target()==='trace'&&this.tab==='process')this.setTab('trace');});
    this.restoreRuntimeEndpoints();
    this.sessions.load();
    // Sync localStorage session into ChatSessionsService so fallback session_id is correct
    if (this._snakeSessionId && this._snakeSessionId !== 'ananta-settings') {
      this.sessions.activeSessionId$.next(this._snakeSessionId);
    }
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
    this.sessionSub = this.sessions.sessions$.subscribe(items => {
      if (!items.length || items.some(item => item.id === this._snakeSessionId)) return;
      const fallback = items.find(item => !item.settings?.['chat_read_only']) || items[0];
      this.switchSession(fallback.id);
    });
  }

  ngOnDestroy(): void {
    this.historySub?.unsubscribe();
    this.activeSub?.unsubscribe();
    this.sessionSub?.unsubscribe();
    if (this.summaryHintTimer) clearTimeout(this.summaryHintTimer);
    this.uiSync.stop();
  }

  chatMessages(): ChatHistoryMessage[] {
    return this.history.getVisibleMessages(this._snakeSessionId || 'default');
  }

  contextMessageCount(): number {
    return this.history.getContextMessages(this._snakeSessionId || 'default').length;
  }

  toggleContext(message: ChatHistoryMessage): void {
    this.history.setIncludedInContext(
      this._snakeSessionId || 'default', message.id, message.includedInContext === false,
    );
  }

  beginEdit(message: ChatHistoryMessage): void {
    this.editingMessageId = message.id;
    this.editingMessageText = message.text;
  }

  saveEdit(message: ChatHistoryMessage): void {
    if (this.history.updateMessage(this._snakeSessionId || 'default', message.id, this.editingMessageText)) {
      this.cancelEdit();
    }
  }

  cancelEdit(): void {
    this.editingMessageId = '';
    this.editingMessageText = '';
  }

  deleteMessage(message: ChatHistoryMessage): void {
    if (!window.confirm('Diese Nachricht aus dem lokalen Chatverlauf löschen?')) return;
    this.history.deleteMessage(this._snakeSessionId || 'default', message.id);
    this.selectedMsgIds.delete(message.id);
    if (this.editingMessageId === message.id) this.cancelEdit();
  }

  regenerateMessage(message: ChatHistoryMessage): void {
    const sessionId = this._snakeSessionId || 'default';
    const request = this.history.regenerationRequest(sessionId, message.id);
    if (!request) return;
    const outgoingId = this.svc.sendRoomMessage(request.prompt, sessionId, request.context);
    if (outgoingId) this.history.replaceWithNextAiMessage(sessionId, message.id, outgoingId);
  }

  toggleSelectMode(): void {
    this.selectMode = !this.selectMode;
    if (!this.selectMode) this.selectedMsgIds.clear();
  }

  toggleMsgSelection(id: string): void {
    if (this.selectedMsgIds.has(id)) {
      this.selectedMsgIds.delete(id);
    } else {
      this.selectedMsgIds.add(id);
    }
  }

  onMsgRowClick(id: string): void {
    if (this.selectMode) this.toggleMsgSelection(id);
  }

  toggleExpandSummary(id: string): void {
    if (this.expandedSummaryIds.has(id)) {
      this.expandedSummaryIds.delete(id);
    } else {
      this.expandedSummaryIds.add(id);
    }
  }

  summarizedMessages(summaryId: string): ChatHistoryMessage[] {
    return this.history.getSummarizedMessages(this._snakeSessionId || 'default', summaryId);
  }

  dissolveSummary(summaryId: string): void {
    this.history.dissolveSummary(this._snakeSessionId || 'default', summaryId);
    this.expandedSummaryIds.delete(summaryId);
    this.selectedMsgIds.delete(summaryId);
  }

  summarizeSelected(): void {
    const sessionId = this._snakeSessionId || 'default';
    const all = this.history.getVisibleMessages(sessionId);
    const selected = all.filter(m => this.selectedMsgIds.has(m.id));
    if (selected.length < 2) return;
    this.summarizing = true;
    const payload = selected.map(m => ({ sender: m.isAI ? 'ai' : 'user', text: m.text }));
    this.sessions.summarizeMessages(sessionId, payload, this.summaryLength).subscribe({
      next: res => {
        this.history.insertSummary(sessionId, res.summary, selected.map(m => m.id));
        this.summarizing = false;
        this.selectMode = false;
        this.selectedMsgIds.clear();
        if (res.method === 'extractive') {
          this.setSummaryHint('ℹ Kein LLM erreichbar — extraktive Zusammenfassung verwendet.');
        }
      },
      error: () => {
        this.summarizing = false;
        this.setSummaryHint('⚠ Zusammenfassung fehlgeschlagen.');
      },
    });
  }

  private setSummaryHint(text: string): void {
    this.summaryHint = text;
    if (this.summaryHintTimer) clearTimeout(this.summaryHintTimer);
    this.summaryHintTimer = setTimeout(() => { this.summaryHint = ''; }, 6000);
  }

  sendPlaceholder(): string {
    const sess = this.activeSessionFor(this._snakeSessionId);
    if (sess && this.isSessionReadOnly(sess)) {
      return 'Read-only Log-Session — nur Backend schreibt hier';
    }
    return sess ? `Nachricht in "${sess.name}"…` : 'Nachricht senden…';
  }

  isSessionReadOnly(s: ChatSession | null | undefined): boolean {
    return !!s?.settings?.['chat_read_only'];
  }

  isActiveSessionReadOnly(): boolean {
    return this.isSessionReadOnly(this.activeSessionFor(this._snakeSessionId));
  }

  isVisualLogSession(): boolean {
    return this._snakeSessionId === 'ananta-visual';
  }

  createChat(): void {
    const name = this.newChatName.trim();
    if (!name) return;
    const profile = this.sessions.profiles$.value.find(item => item.id === this.newChatProfileId);
    this.sessions.create({
      name,
      icon: profile?.icon || '💬',
      profile_id: this.newChatProfileId,
      system_prompt: '',
      settings: {},
    });
    this.newChatName = '';
    this.newChatMode = false;
  }

  get showRegistration(): boolean {
    return this.bridge.showRegistration;
  }

  registerWithKeycloak(): void {
    this.oidc.registerWithKeycloak();
  }

  async keycloakLogin(): Promise<void> {
    this.loginError = '';
    this.loginBusy = true;
    try {
      await this.oidc.startLoginPopup();
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
    if (this.isActiveSessionReadOnly()) return;  // guard: never post to read-only log sessions
    const contextHistory = this.history.getContextMessages(this._snakeSessionId || 'default').slice(-20).map(message => ({
      role: message.isAI ? 'assistant' as const : 'user' as const,
      content: message.text,
    }));
    this.svc.sendRoomMessage(text, this._snakeSessionId, contextHistory);
    this.draft = '';
  }

  cancelChat(): void {
    this.svc.cancelChat();
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

  private loadSnakeSession(): string {
    try {
      return localStorage.getItem('ananta.snake.session') || 'ananta-settings';
    } catch { return 'ananta-settings'; }
  }

  switchSession(id: string): void {
    this._snakeSessionId = id;
    this.selectMode = false;
    this.selectedMsgIds.clear();
    this.expandedSummaryIds.clear();
    try { localStorage.setItem('ananta.snake.session', id); } catch {}
    this.sessions.activate(id);
  }

  sessionGroups(): Array<{ name: string; sessions: ChatSession[] }> {
    const all = this.sessions.sessions$.value || [];
    const folderNames = new Map((this.sessions.folders$.value || []).map(folder => [folder.id, folder.name]));
    const map = new Map<string, ChatSession[]>();
    for (const s of all) {
      const g = folderNames.get(s.folder_id || '') || '';
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

  setTab(tab: AiSnakePanelTab): void {
    this.tab = tab;
    this.tabChange.emit(tab);
  }

  openPairDev(): void {
    this.pairDevRequested.emit();
  }

  private restoreRuntimeEndpoints(): void {
    try {
      const raw = localStorage.getItem('ananta.ai-snake.runtime-endpoints.v1');
      if (!raw) return;
      const parsed = JSON.parse(raw);
      const webrtc = String(parsed?.webrtcBaseUrl || '').trim();
      if (webrtc) this.webrtcBaseUrl = webrtc;
    } catch {}
  }

  persistRuntimeEndpoints(): void {
    try {
      localStorage.setItem(
        'ananta.ai-snake.runtime-endpoints.v1',
        JSON.stringify({
          webrtcBaseUrl: this.webrtcBaseUrl.trim(),
        }),
      );
    } catch {}
  }
}
