import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ShareSessionService, ShareParticipant } from '../services/share-session.service';

type PanelView = 'home' | 'create' | 'join' | 'active';

@Component({
  selector: 'app-ai-snake-share-panel',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="share-panel">
      <div class="share-header">
        <span>⇄ Session Sharing</span>
        @if (svc.isActive) {
          <span class="share-badge active">● aktiv</span>
        }
      </div>

      @if (!svc.isActive) {
        <!-- Home: Aktionen -->
        @if (view === 'home') {
          <div class="share-actions">
            <button class="share-btn primary" (click)="view = 'create'">+ Session erstellen</button>
            <button class="share-btn" (click)="view = 'join'">Code eingeben</button>
          </div>
        }

        <!-- Create session -->
        @if (view === 'create') {
          <div class="share-form">
            <div class="share-form-title">Neue Session</div>
            <label class="share-label">Titel
              <input class="share-input" [(ngModel)]="createTitle" placeholder="z.B. AI-Snake Demo">
            </label>
            <div class="share-label">Permissions</div>
            <div class="share-checks">
              <label><input type="checkbox" [(ngModel)]="perm_chat"> Chat</label>
              <label><input type="checkbox" [(ngModel)]="perm_view"> TUI-View</label>
              <label><input type="checkbox" [(ngModel)]="perm_cursor"> Cursor</label>
            </div>
            <label class="share-label">Ablauf
              <select class="share-select" [(ngModel)]="expiresIn">
                <option value="0">Kein Ablauf</option>
                <option value="3600">1 Stunde</option>
                <option value="86400">24 Stunden</option>
                <option value="604800">7 Tage</option>
              </select>
            </label>
            <div class="share-form-actions">
              <button class="share-btn primary" (click)="doCreate()" [disabled]="creating">
                {{ creating ? 'Erstelle...' : 'Erstellen' }}
              </button>
              <button class="share-btn" (click)="view = 'home'">Abbrechen</button>
            </div>
            @if (createError) { <div class="share-error">{{ createError }}</div> }
          </div>
        }

        <!-- Join session -->
        @if (view === 'join') {
          <div class="share-form">
            <div class="share-form-title">Session beitreten</div>
            <label class="share-label">Invite-Code
              <input class="share-input mono" [(ngModel)]="joinCode" placeholder="z.B. abc123xyz" maxlength="16">
            </label>
            <div class="share-form-actions">
              <button class="share-btn primary" (click)="doJoin()" [disabled]="joining || !joinCode.trim()">
                {{ joining ? 'Verbinde...' : 'Beitreten' }}
              </button>
              <button class="share-btn" (click)="view = 'home'">Abbrechen</button>
            </div>
            @if (joinError) { <div class="share-error">{{ joinError }}</div> }
          </div>
        }
      }

      @if (svc.isActive) {
        <!-- Active session -->
        @let state = svc.state$ | async;
        @if (state) {
          <div class="share-session-info">
            <div class="share-session-title">{{ state.session?.title }}</div>
            <div class="share-meta">
              <span class="share-badge {{ state.role }}">{{ state.role === 'owner' ? 'Eigentümer' : 'Teilnehmer' }}</span>
              <span class="share-meta-code">Code: <strong>{{ state.session?.invite_code }}</strong></span>
              <button class="share-copy-btn" (click)="copyCode(state.session?.invite_code ?? '')">⎘</button>
            </div>
          </div>

          <!-- Tabs -->
          <div class="share-tabs">
            <button class="share-tab" [class.active]="activeTab === 'chat'" (click)="activeTab = 'chat'">Chat</button>
            <button class="share-tab" [class.active]="activeTab === 'participants'" (click)="activeTab = 'participants'">
              Teilnehmer ({{ state.participants.length }})
            </button>
          </div>

          <!-- Chat tab -->
          @if (activeTab === 'chat') {
            <div class="share-chat-msgs" #chatBox>
              @for (msg of state.messages; track msg.id) {
                <div class="share-msg" [class.own]="isOwnMessage(msg.sender_id)">
                  <span class="share-msg-sender">{{ msg.sender_id }}</span>
                  <span class="share-msg-text">{{ msg.text }}</span>
                </div>
              }
              @if (!state.messages.length) {
                <div class="share-empty">Noch keine Nachrichten.</div>
              }
            </div>
            <div class="share-chat-input-row">
              <input class="share-chat-input" [(ngModel)]="chatInput" placeholder="Nachricht..."
                (keydown.enter)="sendMsg()" [disabled]="!canChat(state)">
              <button class="share-send-btn" (click)="sendMsg()" [disabled]="!chatInput.trim() || !canChat(state)">→</button>
            </div>
          }

          <!-- Participants tab -->
          @if (activeTab === 'participants') {
            <div class="share-participants">
              @for (p of state.participants; track p.id) {
                <div class="share-participant" [class.revoked]="!!p.revoked_at">
                  <div class="share-p-row">
                    <span class="share-p-id">{{ p.user_id || p.device_id }}</span>
                    <span class="share-p-status" [class.online]="svc.participantStatus(p) === 'online'">
                      {{ svc.participantStatus(p) }}
                    </span>
                    @if (state.role === 'owner' && !p.revoked_at) {
                      <button class="share-revoke-btn" (click)="revoke(p)" title="Sperren">✕</button>
                    }
                  </div>
                  <div class="share-p-perms">
                    @for (perm of permEntries(p.permissions); track perm.key) {
                      <span class="share-perm-chip" [class.on]="perm.val">{{ perm.key }}</span>
                    }
                  </div>
                </div>
              }
              @if (!state.participants.length) {
                <div class="share-empty">Noch keine Teilnehmer.</div>
              }
            </div>
          }

          <!-- End / Leave -->
          <div class="share-footer">
            @if (state.role === 'owner') {
              <button class="share-btn danger" (click)="doEnd()">Session beenden</button>
            } @else {
              <button class="share-btn" (click)="svc.leaveSession()">Verlassen</button>
            }
          </div>
        }
      }
    </div>
  `,
  styles: [`
    :host { font-family: ui-monospace, Menlo, Consolas, monospace; }
    .share-panel { display: flex; flex-direction: column; height: 100%; background: #0b1220; color: #c8d8f8; font-size: 12px; }
    .share-header { padding: 7px 10px; border-bottom: 1px solid #1a2d4a; background: #0d1828; font-weight: 600; display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
    .share-badge { font-size: 10px; padding: 1px 6px; border-radius: 2px; border: 1px solid #1a2d4a; }
    .share-badge.active { color: #7fffd4; border-color: #7fffd4; }
    .share-badge.owner { color: #fbbf24; border-color: #7a5a10; }
    .share-badge.participant { color: #a8c7ff; border-color: #2a4070; }
    .share-actions { display: flex; flex-direction: column; gap: 8px; padding: 12px 10px; }
    .share-btn {
      border: 1px solid #1a2d4a; border-radius: 3px; padding: 6px 10px; background: transparent;
      color: #6b8ab8; cursor: pointer; font-size: 12px; font-family: inherit; text-align: left;
    }
    .share-btn:hover:not([disabled]) { border-color: #2a4070; color: #c8d8f8; }
    .share-btn.primary { background: #162444; border-color: #2a4070; color: #a8c7ff; }
    .share-btn.primary:hover:not([disabled]) { background: #1e3058; border-color: #7fffd4; color: #7fffd4; }
    .share-btn.danger { color: #fb7185; border-color: #4a1a1a; background: #1a0a0a; }
    .share-btn[disabled] { opacity: 0.4; cursor: not-allowed; }
    .share-form { padding: 10px; display: flex; flex-direction: column; gap: 8px; }
    .share-form-title { font-weight: 600; color: #a8c7ff; margin-bottom: 2px; }
    .share-label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: #6b8ab8; }
    .share-input, .share-select {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      padding: 4px 7px; font-size: 11px; font-family: inherit; border-radius: 2px;
    }
    .share-input.mono { letter-spacing: 0.1em; }
    .share-checks { display: flex; gap: 12px; font-size: 11px; }
    .share-checks label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
    .share-form-actions { display: flex; gap: 8px; }
    .share-error { color: #fb7185; font-size: 11px; }
    .share-session-info { padding: 8px 10px; border-bottom: 1px solid #1a2d4a; }
    .share-session-title { font-weight: 600; color: #a8c7ff; margin-bottom: 4px; }
    .share-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .share-meta-code { font-size: 11px; color: #6b8ab8; }
    .share-meta-code strong { color: #7fffd4; letter-spacing: 0.08em; }
    .share-copy-btn { background: none; border: 1px solid #1a2d4a; color: #6b8ab8; cursor: pointer; padding: 1px 5px; border-radius: 2px; font-size: 12px; }
    .share-copy-btn:hover { border-color: #7fffd4; color: #7fffd4; }
    .share-tabs { display: flex; border-bottom: 1px solid #1a2d4a; flex-shrink: 0; }
    .share-tab { flex: 1; padding: 5px; background: none; border: none; border-bottom: 2px solid transparent; color: #4a6a9a; cursor: pointer; font-family: inherit; font-size: 11px; }
    .share-tab.active { color: #7fffd4; border-bottom-color: #7fffd4; }
    .share-chat-msgs { flex: 1; overflow-y: auto; padding: 6px 8px; min-height: 0; max-height: 200px; }
    .share-chat-msgs::-webkit-scrollbar { width: 4px; }
    .share-chat-msgs::-webkit-scrollbar-thumb { background: #1a2d4a; }
    .share-msg { margin-bottom: 5px; display: flex; flex-direction: column; }
    .share-msg.own .share-msg-text { background: #162238; border-color: #2a4070; color: #a8c7ff; align-self: flex-end; }
    .share-msg-sender { font-size: 10px; color: #4a6a9a; margin-bottom: 2px; }
    .share-msg-text { background: #0f1c30; border: 1px solid #1a3058; padding: 4px 8px; border-radius: 2px; color: #c8d8f8; display: inline-block; max-width: 90%; word-break: break-word; }
    .share-chat-input-row { display: flex; gap: 6px; padding: 6px 8px; border-top: 1px solid #1a2d4a; flex-shrink: 0; }
    .share-chat-input { flex: 1; background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8; padding: 4px 7px; font-size: 11px; font-family: inherit; border-radius: 2px; }
    .share-send-btn { background: #162444; border: 1px solid #2a4070; color: #a8c7ff; padding: 4px 10px; cursor: pointer; border-radius: 2px; font-size: 13px; }
    .share-send-btn:hover:not([disabled]) { border-color: #7fffd4; color: #7fffd4; }
    .share-participants { flex: 1; overflow-y: auto; padding: 6px 8px; max-height: 200px; }
    .share-participant { padding: 5px 0; border-bottom: 1px solid #0f1828; }
    .share-participant.revoked { opacity: 0.4; }
    .share-p-row { display: flex; align-items: center; gap: 8px; }
    .share-p-id { flex: 1; font-size: 11px; color: #a8c7ff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .share-p-status { font-size: 10px; color: #4a6a9a; }
    .share-p-status.online { color: #7fffd4; }
    .share-revoke-btn { background: none; border: 1px solid #4a1a1a; color: #fb7185; cursor: pointer; padding: 1px 5px; border-radius: 2px; font-size: 10px; }
    .share-p-perms { display: flex; gap: 4px; margin-top: 3px; flex-wrap: wrap; }
    .share-perm-chip { font-size: 9px; padding: 1px 5px; border: 1px solid #131e36; border-radius: 2px; color: #2a4070; }
    .share-perm-chip.on { color: #7fffd4; border-color: #1a4a2a; }
    .share-empty { color: #2a4070; font-size: 11px; padding: 8px 0; }
    .share-footer { padding: 8px 10px; border-top: 1px solid #1a2d4a; flex-shrink: 0; }
  `],
})
export class AiSnakeSharePanelComponent {
  svc = inject(ShareSessionService);

  view: PanelView = 'home';
  activeTab: 'chat' | 'participants' = 'chat';

  createTitle = '';
  perm_chat = true;
  perm_view = false;
  perm_cursor = false;
  expiresIn = '86400';
  creating = false;
  createError = '';

  joinCode = '';
  joining = false;
  joinError = '';

  chatInput = '';

  async doCreate(): Promise<void> {
    if (!this.createTitle.trim()) { this.createError = 'Titel erforderlich'; return; }
    this.creating = true;
    this.createError = '';
    try {
      await this.svc.createSession(this.createTitle.trim(), {
        chat: this.perm_chat, view_tui: this.perm_view, remote_cursor: this.perm_cursor,
      }, Number(this.expiresIn) || null);
      this.activeTab = 'chat';
    } catch (e: any) {
      this.createError = String(e?.message ?? 'Erstellen fehlgeschlagen');
    } finally {
      this.creating = false;
    }
  }

  async doJoin(): Promise<void> {
    if (!this.joinCode.trim()) return;
    this.joining = true;
    this.joinError = '';
    try {
      await this.svc.joinSession(this.joinCode.trim());
      this.activeTab = 'chat';
    } catch (e: any) {
      this.joinError = String(e?.message ?? 'Beitreten fehlgeschlagen');
    } finally {
      this.joining = false;
    }
  }

  sendMsg(): void {
    if (!this.chatInput.trim()) return;
    this.svc.sendMessage(this.chatInput.trim());
    this.chatInput = '';
  }

  doEnd(): void {
    if (!confirm('Session wirklich beenden? Alle Teilnehmer werden getrennt.')) return;
    this.svc.endSession();
    this.view = 'home';
  }

  revoke(p: ShareParticipant): void {
    if (!confirm(`Teilnehmer "${p.user_id || p.device_id}" sperren?`)) return;
    this.svc.revokeParticipant(p.id);
  }

  canChat(state: any): boolean {
    return state?.role === 'owner' || !!state?.session?.permissions?.chat;
  }

  isOwnMessage(senderId: string): boolean {
    return false; // TODO: wiring with auth service
  }

  permEntries(perms: Record<string, boolean>): Array<{ key: string; val: boolean }> {
    return Object.entries(perms ?? {}).map(([key, val]) => ({ key, val }));
  }

  async copyCode(code: string): Promise<void> {
    if (!code) return;
    await navigator.clipboard.writeText(code).catch(() => {});
  }
}
