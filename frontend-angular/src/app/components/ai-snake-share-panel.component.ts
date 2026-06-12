import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ShareSessionService, ShareParticipant } from '../services/share-session.service';
import { HubApiCoreService } from '../services/hub-api-core.service';
import { AgentDirectoryService } from '../services/agent-directory.service';

type PanelView = 'home' | 'create' | 'join' | 'active';
type MainTab = 'share' | 'groups';

interface PairGroup {
  id: string;
  name: string;
  description: string;
  default_permissions: Record<string, boolean>;
  created_at: number;
}

interface PairGroupMember {
  id: string;
  group_id: string;
  user_id: string;
  display_name: string;
}

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
        <div class="main-tabs">
          <button class="main-tab" [class.active]="mainTab === 'share'" (click)="mainTab = 'share'">Sessions</button>
          <button class="main-tab" [class.active]="mainTab === 'groups'" (click)="switchToGroups()">Gruppen</button>
        </div>
      </div>

      <!-- ── GRUPPEN-TAB ── -->
      @if (mainTab === 'groups') {
        <div class="groups-panel">
          @if (groupView === 'list') {
            <div class="groups-toolbar">
              <button class="share-btn primary" (click)="startCreateGroup()">+ Gruppe</button>
            </div>
            @if (groupsLoading) { <div class="share-empty">Lade…</div> }
            @for (g of groups; track g.id) {
              <div class="group-row" (click)="openGroup(g)">
                <span class="group-name">{{ g.name }}</span>
                <span class="group-desc">{{ g.description }}</span>
                <button class="share-btn sm" (click)="$event.stopPropagation(); createGroupSession(g)">Einladen</button>
                <button class="share-revoke-btn" (click)="$event.stopPropagation(); deleteGroup(g)">✕</button>
              </div>
            }
            @if (!groupsLoading && !groups.length) {
              <div class="share-empty">Keine Gruppen. Erstelle deine erste Gruppe.</div>
            }
          }
          @if (groupView === 'create') {
            <div class="share-form">
              <div class="share-form-title">Neue Gruppe</div>
              <label class="share-label">Name <input class="share-input" [(ngModel)]="newGroupName" placeholder="z.B. Dev-Team"></label>
              <label class="share-label">Beschreibung <input class="share-input" [(ngModel)]="newGroupDesc" placeholder="Optional"></label>
              <div class="share-label">Standard-Permissions</div>
              <div class="share-checks">
                <label><input type="checkbox" [(ngModel)]="gperm_chat"> Chat</label>
                <label><input type="checkbox" [(ngModel)]="gperm_view"> TUI-View</label>
                <label><input type="checkbox" [(ngModel)]="gperm_cursor"> Cursor</label>
              </div>
              <div class="share-form-actions">
                <button class="share-btn primary" (click)="doCreateGroup()" [disabled]="creatingGroup">
                  {{ creatingGroup ? 'Erstelle…' : 'Erstellen' }}
                </button>
                <button class="share-btn" (click)="groupView = 'list'">Abbrechen</button>
              </div>
              @if (groupError) { <div class="share-error">{{ groupError }}</div> }
            </div>
          }
          @if (groupView === 'detail' && selectedGroup) {
            <div class="group-detail">
              <div class="share-form-title">{{ selectedGroup.name }}</div>
              <div class="group-members-header">
                <span class="share-label">Mitglieder ({{ groupMembers.length }})</span>
              </div>
              @for (m of groupMembers; track m.id) {
                <div class="group-member-row">
                  <span class="group-member-name">{{ m.display_name || m.user_id }}</span>
                  <button class="share-revoke-btn" (click)="removeMember(m)">✕</button>
                </div>
              }
              @if (!groupMembers.length) { <div class="share-empty">Noch keine Mitglieder.</div> }
              <div class="add-member-row">
                <input class="share-input" [(ngModel)]="newMemberId" placeholder="User-ID hinzufügen" />
                <button class="share-btn sm" (click)="addMember()" [disabled]="!newMemberId.trim()">+</button>
              </div>
              @if (memberError) { <div class="share-error">{{ memberError }}</div> }
              <div class="share-form-actions" style="margin-top:8px">
                <button class="share-btn primary" (click)="createGroupSession(selectedGroup)">Session für Gruppe</button>
                <button class="share-btn" (click)="groupView = 'list'; selectedGroup = null">Zurück</button>
              </div>
              @if (groupSessionInvite) {
                <div class="invite-result">
                  <span class="share-meta-code">Invite-Code: <strong>{{ groupSessionInvite }}</strong></span>
                  <button class="share-copy-btn" (click)="copyCode(groupSessionInvite)">⎘</button>
                </div>
              }
            </div>
          }
        </div>
      }

      <!-- ── SESSIONS-TAB ── -->
      @if (mainTab === 'share') {

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

      } <!-- end @if mainTab === 'share' -->
    </div>
  `,
  styles: [`
    :host { font-family: ui-monospace, Menlo, Consolas, monospace; }
    .share-panel { display: flex; flex-direction: column; height: 100%; background: #0b1220; color: #c8d8f8; font-size: 12px; }
    .share-header { padding: 7px 10px; border-bottom: 1px solid #1a2d4a; background: #0d1828; font-weight: 600; display: flex; align-items: center; gap: 8px; flex-shrink: 0; flex-wrap: wrap; }
    .main-tabs { margin-left: auto; display: flex; gap: 0; border: 1px solid #1a2d4a; border-radius: 3px; overflow: hidden; }
    .main-tab { background: transparent; border: none; border-right: 1px solid #1a2d4a; color: #4a6a9a; cursor: pointer; font-size: 10px; font-family: inherit; padding: 2px 8px; }
    .main-tab:last-child { border-right: none; }
    .main-tab.active { color: #7fffd4; background: #102238; }
    .groups-panel { flex: 1; overflow-y: auto; padding: 8px 10px; }
    .groups-toolbar { margin-bottom: 8px; }
    .group-row { display: flex; align-items: center; gap: 6px; padding: 5px 0; border-bottom: 1px solid #0f1828; cursor: pointer; }
    .group-row:hover { background: #0d1828; }
    .group-name { font-weight: 600; color: #a8c7ff; flex: 1; }
    .group-desc { font-size: 10px; color: #4a6a9a; flex: 2; }
    .share-btn.sm { padding: 2px 7px; font-size: 10px; }
    .group-detail { padding: 4px 0; }
    .group-members-header { margin: 6px 0 4px; }
    .group-member-row { display: flex; align-items: center; gap: 6px; padding: 3px 0; border-bottom: 1px solid #0f1828; }
    .group-member-name { flex: 1; color: #a8c7ff; font-size: 11px; }
    .add-member-row { display: flex; gap: 6px; margin-top: 8px; }
    .invite-result { margin-top: 8px; display: flex; align-items: center; gap: 6px; background: #0d1828; padding: 6px 8px; border-radius: 3px; }
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
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);

  mainTab: MainTab = 'share';
  view: PanelView = 'home';
  activeTab: 'chat' | 'participants' = 'chat';

  // Session creation
  createTitle = '';
  perm_chat = true;
  perm_view = false;
  perm_cursor = false;
  expiresIn = '86400';
  creating = false;
  createError = '';

  // Session join
  joinCode = '';
  joining = false;
  joinError = '';

  chatInput = '';

  // Groups
  groupView: 'list' | 'create' | 'detail' = 'list';
  groups: PairGroup[] = [];
  groupsLoading = false;
  selectedGroup: PairGroup | null = null;
  groupMembers: PairGroupMember[] = [];
  newGroupName = '';
  newGroupDesc = '';
  gperm_chat = true;
  gperm_view = false;
  gperm_cursor = false;
  creatingGroup = false;
  groupError = '';
  newMemberId = '';
  memberError = '';
  groupSessionInvite = '';

  private get hubUrl(): string {
    return this.dir.list().find((a) => a.role === 'hub')?.url ?? '';
  }

  switchToGroups(): void {
    this.mainTab = 'groups';
    this.loadGroups();
  }

  private loadGroups(): void {
    this.groupsLoading = true;
    const url = this.hubUrl;
    this.core.get<{ ok: boolean; groups: PairGroup[] }>(`${url}/pair-groups`, url).subscribe({
      next: (r) => { this.groups = r?.groups ?? []; this.groupsLoading = false; },
      error: () => { this.groupsLoading = false; },
    });
  }

  startCreateGroup(): void {
    this.newGroupName = '';
    this.newGroupDesc = '';
    this.gperm_chat = true;
    this.gperm_view = false;
    this.gperm_cursor = false;
    this.groupError = '';
    this.groupView = 'create';
  }

  doCreateGroup(): void {
    if (!this.newGroupName.trim()) { this.groupError = 'Name erforderlich'; return; }
    this.creatingGroup = true;
    this.groupError = '';
    const url = this.hubUrl;
    this.core.post<{ ok: boolean; group: PairGroup }>(`${url}/pair-groups`, {
      name: this.newGroupName.trim(),
      description: this.newGroupDesc.trim(),
      default_permissions: { chat: this.gperm_chat, view_tui: this.gperm_view, cursor: this.gperm_cursor },
    }, url).subscribe({
      next: (r) => {
        if (r?.group) this.groups = [...this.groups, r.group];
        this.creatingGroup = false;
        this.groupView = 'list';
      },
      error: (e) => {
        this.groupError = String(e?.error?.error ?? 'Erstellen fehlgeschlagen');
        this.creatingGroup = false;
      },
    });
  }

  openGroup(g: PairGroup): void {
    this.selectedGroup = g;
    this.groupMembers = [];
    this.newMemberId = '';
    this.memberError = '';
    this.groupSessionInvite = '';
    const url = this.hubUrl;
    this.core.get<{ ok: boolean; group: PairGroup; members: PairGroupMember[] }>(
      `${url}/pair-groups/${g.id}`, url,
    ).subscribe({
      next: (r) => { this.groupMembers = r?.members ?? []; this.groupView = 'detail'; },
      error: () => { this.groupView = 'detail'; },
    });
  }

  addMember(): void {
    const uid = this.newMemberId.trim();
    if (!uid || !this.selectedGroup) return;
    this.memberError = '';
    const url = this.hubUrl;
    this.core.post<{ ok: boolean; member: PairGroupMember }>(
      `${url}/pair-groups/${this.selectedGroup.id}/members`,
      { user_id: uid, display_name: uid }, url,
    ).subscribe({
      next: (r) => {
        if (r?.member) this.groupMembers = [...this.groupMembers, r.member];
        this.newMemberId = '';
      },
      error: (e) => { this.memberError = String(e?.error?.error ?? 'Hinzufügen fehlgeschlagen'); },
    });
  }

  removeMember(m: PairGroupMember): void {
    if (!this.selectedGroup) return;
    const url = this.hubUrl;
    this.core.delete(`${url}/pair-groups/${this.selectedGroup.id}/members/${m.user_id}`, url).subscribe({
      next: () => { this.groupMembers = this.groupMembers.filter((x) => x.id !== m.id); },
      error: () => {},
    });
  }

  deleteGroup(g: PairGroup): void {
    if (!confirm(`Gruppe "${g.name}" löschen?`)) return;
    const url = this.hubUrl;
    this.core.delete(`${url}/pair-groups/${g.id}`, url).subscribe({
      next: () => { this.groups = this.groups.filter((x) => x.id !== g.id); },
      error: () => {},
    });
  }

  createGroupSession(g: PairGroup): void {
    this.groupSessionInvite = '';
    const url = this.hubUrl;
    this.core.post<{ ok: boolean; session: any; invite_code: string }>(
      `${url}/pair-groups/${g.id}/invite`, {}, url,
    ).subscribe({
      next: (r) => { this.groupSessionInvite = r?.invite_code ?? ''; },
      error: () => {},
    });
  }

  // Session methods
  async doCreate(): Promise<void> {
    if (!this.createTitle.trim()) { this.createError = 'Titel erforderlich'; return; }
    this.creating = true;
    this.createError = '';
    try {
      await this.svc.createSession(this.createTitle.trim(), {
        chat: this.perm_chat, view_tui: this.perm_view, cursor: this.perm_cursor,
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
    const uid = this.svc.currentUserId;
    return !!uid && senderId === uid;
  }

  permEntries(perms: Record<string, boolean>): Array<{ key: string; val: boolean }> {
    return Object.entries(perms ?? {}).map(([key, val]) => ({ key, val }));
  }

  async copyCode(code: string): Promise<void> {
    if (!code) return;
    await navigator.clipboard.writeText(code).catch(() => {});
  }
}
