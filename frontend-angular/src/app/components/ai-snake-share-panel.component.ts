import { Component, Input, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ShareSessionService, ShareParticipant } from '../services/share-session.service';
import { HubApiCoreService } from '../services/hub-api-core.service';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { ChatMessageComponent } from './chat-message.component';
import { PairViewSessionBindingService } from '../services/pair-view-session-binding.service';
import { PairViewSyncService } from '../services/pair-view-sync.service';
import { PairSecurityBootstrapState } from '../services/pair-view-security-bootstrap.service';
import { PairSessionCatalogComponent } from './pair-session-catalog.component';
import {
  pairSessionErrorCode,
  pairSessionErrorMessage,
} from '../services/pair-session-error-message';
import { RemoteViewProjection } from '../services/pair-view-sync.types';
import { PairGroup, PairGroupMember } from './pair-group.types';
import {
  RemoteViewEntry,
  pairSecurityFingerprint,
  pairSecurityLabel,
  permissionEntries,
  toRemoteViewEntries,
} from './ai-snake-share-panel.view-models';
import { AI_SNAKE_SHARE_PANEL_STYLES } from './ai-snake-share-panel.styles';

type PanelView = 'home' | 'create' | 'join' | 'catalog';
type MainTab = 'share' | 'groups';

@Component({
  selector: 'app-ai-snake-share-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, ChatMessageComponent, PairSessionCatalogComponent],
  template: `
    <div class="share-panel">
      <div class="share-header">
        <span>⇄ Session Sharing</span>
        @if (svc.isActive) {
          <span class="share-badge active">● aktiv</span>
        }
        <div class="main-tabs">
          <button class="main-tab" [class.active]="mainTab === 'share'" (click)="mainTab = 'share'">Sessions</button>
          @if (!publicOnly) {
            <button class="main-tab" [class.active]="mainTab === 'groups'" (click)="switchToGroups()">Gruppen</button>
          }
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

      @if (publicOnly) {
        <div class="pair-session-toolbar" data-testid="pair-session-toolbar">
          <button
            type="button"
            class="share-btn primary quick-share"
            data-testid="quick-compact-pair-share"
            (click)="doQuickCompactShare()"
            [attr.title]="quickCompactShareTitle() || null"
            [disabled]="quickCompactShareDisabled()">
            {{ quickCompactShareLabel() }}
          </button>
          <div class="pair-session-toolbar-secondary">
            <button type="button" class="share-btn sm" data-testid="manage-pair-sessions"
              [class.selected]="view === 'catalog'" (click)="toggleCatalog()"
              [disabled]="creating || joining || sessionActionPending || svc.sessionMutationPending">
              Sessions verwalten
            </button>
            <button type="button" class="share-btn sm" data-testid="create-pair-session"
              [class.selected]="view === 'create'" (click)="openCreate()"
              [disabled]="creating || joining || sessionActionPending || svc.sessionMutationPending">
              + Neue Session
            </button>
            <button type="button" class="share-btn sm" data-testid="join-pair-session"
              [class.selected]="view === 'join'" (click)="openJoin()"
              [disabled]="creating || joining || sessionActionPending || svc.sessionMutationPending">
              Code eingeben
            </button>
          </div>
          <div class="quick-share-note">
            Teilt E2EE nur internen Seitenpfad, Bereich, Scrollposition und Maus als farbige Snake –
            keine Bildschirmpixel, Kamera, Formularwerte oder URL-Parameter.
            @if (svc.isActive && !activeSessionSupportsCompactShare()) {
              <span data-testid="quick-share-parks-current">
                Die aktuelle Session hat diese Rechte nicht und bleibt beim Erstellen der neuen Schnellteilen-Session geparkt.
              </span>
            }
          </div>
          @if (createError && view === 'home') {
            <div class="share-error" role="alert">{{ createError }}</div>
          }
          @if (sessionActionError && !svc.isActive) {
            <div class="share-error" data-testid="pair-session-action-error" role="alert">
              {{ sessionActionError }}
            </div>
          }
        </div>
      }

      @if (!svc.isActive && view === 'home' && !publicOnly) {
        <!-- Legacy/Hub Home: Aktionen -->
        <div class="share-actions">
          <button class="share-btn primary" (click)="view = 'create'">+ Session erstellen</button>
          <button class="share-btn" (click)="view = 'join'">Code eingeben</button>
          @if (sessionActionError) {
            <div class="share-error" data-testid="pair-session-action-error" role="alert">
              {{ sessionActionError }}
            </div>
          }
        </div>
      }

      @if (publicOnly && view === 'catalog') {
        <app-pair-session-catalog
          (opened)="onSessionOpened()"
          (switched)="onSessionSwitched()"
        />
      }

      @if ((!svc.isActive || publicOnly) && view === 'create') {
          <!-- Create session -->
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
              <button class="share-btn" (click)="closeSubView()">Abbrechen</button>
            </div>
            @if (createError) { <div class="share-error">{{ createError }}</div> }
          </div>
      }

      @if ((!svc.isActive || publicOnly) && view === 'join') {
          <!-- Join session -->
          <div class="share-form">
            <div class="share-form-title">Session beitreten</div>
            <label class="share-label">Invite-Code
              <input class="share-input mono" [(ngModel)]="joinCode" placeholder="z.B. abc123xyz" maxlength="16">
            </label>
            <label class="legacy-consent">
              <input type="checkbox" [(ngModel)]="allowLegacyJoin">
              Legacy-Session ohne verbindliches E2EE ausdrücklich zulassen
            </label>
            <div class="share-form-actions">
              <button class="share-btn primary" (click)="doJoin()" [disabled]="joining || !joinCode.trim()">
                {{ joining ? 'Verbinde...' : 'Beitreten' }}
              </button>
              <button class="share-btn" (click)="closeSubView()">Abbrechen</button>
            </div>
            @if (joinError) { <div class="share-error" role="alert">{{ joinError }}</div> }
            @if (pendingJoinRecoveryAvailable) {
              <button
                type="button"
                class="share-btn danger"
                data-testid="discard-pending-join"
                (click)="discardPendingJoinAttempt()"
              >Früheren Beitrittsversuch verwerfen</button>
            }
          </div>
      }

      @if (svc.isActive && view === 'home') {
        <!-- Active session -->
        @let state = svc.state$ | async;
        @if (state) {
          <div class="share-session-info">
            <div class="share-session-title">{{ state.session?.title }}</div>
            <div class="share-meta">
              <span class="share-badge {{ state.role }}">{{ state.role === 'owner' ? 'Eigentümer' : 'Teilnehmer' }}</span>
              @if (state.role === 'owner' && state.session?.invite_code) {
                <span class="share-meta-code">Code: <strong>{{ state.session?.invite_code }}</strong></span>
                <button class="share-copy-btn" (click)="copyCode(state.session?.invite_code ?? '')">⎘</button>
              }
            </div>
            @if (svc.securityState$ | async; as security) {
              <div class="security-line" data-testid="share-security-status"
                [class.ready]="security.status === 'ready'"
                [class.warning]="security.status === 'legacy' || security.status === 'fingerprint_changed'"
                [class.failed]="security.status === 'failed'">
                <span>{{ securityLabel(security) }}</span>
                @if (securityFingerprint(security)) {
                  <code>{{ securityFingerprint(security) }}</code>
                }
                @if (security.status === 'fingerprint_changed') {
                  <button type="button" class="share-btn sm" (click)="svc.approveFingerprintChange()">
                    Fingerprint neu freigeben
                  </button>
                }
              </div>
            }
          </div>

          @if (pairSync.remoteViews$ | async; as peerViews) {
            <div class="compact-peer-state" data-testid="compact-pair-peer-state">
              <strong>Kompakt geteilt</strong>
              @if (!peerViews.size) {
                <span>Warte auf authentifizierten Seitenstatus…</span>
              }
              @for (entry of remoteViewEntries(peerViews); track entry.senderId) {
                <span>{{ entry.label }} · {{ entry.route }} · {{ entry.surface }}</span>
              }
              <small>Nur Ansicht; kein automatisches Folgen oder Fernsteuern.</small>
            </div>
          }

          @if (state.session?.security_epoch) {
            <div class="compact-share-consent" data-testid="compact-share-consent">
              <strong>Meine kompakte Freigabe</strong>
              <span>Gilt nur fuer diese Session und Sicherheitsepoche.</span>
              <div class="compact-share-actions">
                @if (pairSync.isLocalCompactSharingPending) {
                  <button type="button" class="share-btn sm" data-testid="cancel-pending-compact-share"
                    (click)="cancelPendingCompactShare(state.session!.id)">
                    Ausstehende Schnellfreigabe widerrufen
                  </button>
                } @else {
                  @if (state.session?.permissions?.['view_tui']) {
                    <button type="button" class="share-btn sm" data-testid="toggle-own-compact-view"
                      (click)="toggleOwnCompactView(state.session!.id, state.session!.security_epoch!)">
                      {{ pairSync.isLocalViewSharingEnabled ? 'Eigene Ansicht nicht mehr teilen' : 'Eigene Ansicht teilen' }}
                    </button>
                  }
                  @if (state.session?.permissions?.['remote_cursor']) {
                    <button type="button" class="share-btn sm" data-testid="toggle-own-compact-cursor"
                      (click)="toggleOwnCompactCursor(state.session!.id, state.session!.security_epoch!)">
                      {{ pairSync.isLocalCursorSharingEnabled ? 'Eigene Maus nicht mehr teilen' : 'Eigene Maus teilen' }}
                    </button>
                  }
                }
              </div>
            </div>
          }

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
                  <span class="share-msg-text"><app-chat-message [text]="msg.text" /></span>
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
            @if (chatError) { <div class="share-error chat-error" role="alert">{{ chatError }}</div> }
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
              <button
                class="share-btn danger"
                data-testid="end-pair-session"
                (click)="doEnd()"
                [disabled]="sessionActionPending"
              >{{ sessionActionPending ? 'Beende…' : 'Session beenden' }}</button>
            } @else {
              <button
                class="share-btn"
                data-testid="leave-pair-session"
                (click)="doLeave()"
                [disabled]="sessionActionPending"
              >{{ sessionActionPending ? 'Verlasse…' : 'Verlassen' }}</button>
            }
            @if (sessionActionError) {
              <div class="share-error" data-testid="pair-session-action-error" role="alert">
                {{ sessionActionError }}
              </div>
            }
          </div>
        }
      }

      } <!-- end @if mainTab === 'share' -->
    </div>
  `,
  styles: [AI_SNAKE_SHARE_PANEL_STYLES],
})
export class AiSnakeSharePanelComponent implements OnInit {
  svc = inject(ShareSessionService);
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);
  private pairBinding = inject(PairViewSessionBindingService);
  readonly pairSync = inject(PairViewSyncService);

  /** Removes and fences Hub-backed group operations on the public OIDC-only surface. */
  @Input() publicOnly = false;

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
  allowLegacyJoin = false;
  pendingJoinRecoveryAvailable = false;

  chatInput = '';
  chatError = '';
  sessionActionPending = false;
  sessionActionError = '';

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

  ngOnInit(): void {
    this.pairBinding.start();
  }

  switchToGroups(): void {
    if (this.publicOnly) return;
    this.mainTab = 'groups';
    this.loadGroups();
  }

  private loadGroups(): void {
    if (this.publicOnly) return;
    this.groupsLoading = true;
    const url = this.hubUrl;
    this.core.get<{ ok: boolean; groups: PairGroup[] }>(`${url}/pair-groups`, url).subscribe({
      next: (r) => { this.groups = r?.groups ?? []; this.groupsLoading = false; },
      error: () => { this.groupsLoading = false; },
    });
  }

  startCreateGroup(): void {
    if (this.publicOnly) return;
    this.newGroupName = '';
    this.newGroupDesc = '';
    this.gperm_chat = true;
    this.gperm_view = false;
    this.gperm_cursor = false;
    this.groupError = '';
    this.groupView = 'create';
  }

  doCreateGroup(): void {
    if (this.publicOnly) return;
    if (!this.newGroupName.trim()) { this.groupError = 'Name erforderlich'; return; }
    this.creatingGroup = true;
    this.groupError = '';
    const url = this.hubUrl;
    this.core.post<{ ok: boolean; group: PairGroup }>(`${url}/pair-groups`, {
      name: this.newGroupName.trim(),
      description: this.newGroupDesc.trim(),
      default_permissions: { chat: this.gperm_chat, view_tui: this.gperm_view, remote_cursor: this.gperm_cursor },
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
    if (this.publicOnly) return;
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
    if (this.publicOnly) return;
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
    if (this.publicOnly) return;
    if (!this.selectedGroup) return;
    const url = this.hubUrl;
    this.core.delete(`${url}/pair-groups/${this.selectedGroup.id}/members/${m.user_id}`, url).subscribe({
      next: () => { this.groupMembers = this.groupMembers.filter((x) => x.id !== m.id); },
      error: () => {},
    });
  }

  deleteGroup(g: PairGroup): void {
    if (this.publicOnly) return;
    if (!confirm(`Gruppe "${g.name}" löschen?`)) return;
    const url = this.hubUrl;
    this.core.delete(`${url}/pair-groups/${g.id}`, url).subscribe({
      next: () => { this.groups = this.groups.filter((x) => x.id !== g.id); },
      error: () => {},
    });
  }

  createGroupSession(g: PairGroup): void {
    if (this.publicOnly) return;
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
        chat: this.perm_chat, view_tui: this.perm_view, remote_cursor: this.perm_cursor,
      }, Number(this.expiresIn) || null, {
        expectedAuthority: this.publicOnly ? 'public' : undefined,
      });
      this.view = 'home';
      this.activeTab = 'chat';
    } catch (e: unknown) {
      this.createError = pairSessionErrorMessage(e, 'Erstellen fehlgeschlagen');
    } finally {
      this.creating = false;
    }
  }

  async doQuickCompactShare(): Promise<void> {
    if (
      !this.publicOnly
      || this.creating
      || this.joining
      || this.sessionActionPending
      || this.svc.sessionMutationPending
    ) return;
    const active = this.svc.state$.value;
    const session = active.session;
    if (session) {
      this.createError = '';
      if (this.pairSync.isLocalCompactSharingPending) {
        if (!this.pairSync.cancelPendingLocalCompactSharing(session.id)) {
          this.createError = 'Ausstehende Schnellfreigabe konnte nicht widerrufen werden';
        }
        return;
      }
      if (this.pairSync.isLocalViewSharingEnabled || this.pairSync.isLocalCursorSharingEnabled) {
        if (!session.security_epoch || !this.pairSync.setLocalCompactSharing(
          session.id,
          session.security_epoch,
          { view: false, cursor: false },
        )) this.createError = 'Schnellfreigabe konnte nicht beendet werden';
        return;
      }
      if (!this.activeSessionSupportsCompactShare()) {
        await this.createQuickCompactSession();
        return;
      }
      if (
        active.role === 'owner'
        && (!session.security_epoch || this.svc.securityState$.value.status !== 'ready')
      ) {
        if (!this.pairSync.armLocalCompactSharingOnFirstPeerReady(session.id)) {
          this.createError = 'Schnellfreigabe konnte nicht sicher vorgemerkt werden';
        }
        return;
      }
      if (!session.security_epoch) {
        this.createError = 'Schnellfreigabe wartet auf die bestätigte Sicherheitsepoche';
        return;
      }
      if (!this.pairSync.setLocalCompactSharing(session.id, session.security_epoch!, {
        view: true,
        cursor: true,
      })) this.createError = 'Schnellfreigabe konnte nicht aktiviert werden';
      return;
    }
    await this.createQuickCompactSession();
  }

  async doJoin(): Promise<void> {
    if (!this.joinCode.trim()) return;
    this.joining = true;
    this.joinError = '';
    this.pendingJoinRecoveryAvailable = false;
    try {
      await this.svc.joinSession(this.joinCode.trim(), {
        allowLegacy: this.allowLegacyJoin,
        expectedAuthority: this.publicOnly ? 'public' : undefined,
      });
      this.view = 'home';
      this.activeTab = 'chat';
    } catch (e: unknown) {
      this.joinError = pairSessionErrorMessage(e, 'Beitreten fehlgeschlagen');
      this.pendingJoinRecoveryAvailable = pairSessionErrorCode(e)
        === 'public_pair_pending_attempt_conflict';
    } finally {
      this.joining = false;
    }
  }

  discardPendingJoinAttempt(): void {
    if (!this.pendingJoinRecoveryAvailable) return;
    if (!confirm(
      'Nur fortfahren, wenn der frühere Beitrittsversuch sicher nicht erfolgreich war. '
      + 'Gespeicherten Versuch lokal verwerfen?',
    )) return;
    this.svc.discardPendingJoinAttempt();
    this.pendingJoinRecoveryAvailable = false;
    this.joinError = '';
  }

  toggleCatalog(): void {
    if (
      !this.publicOnly
      || this.creating
      || this.joining
      || this.sessionActionPending
      || this.svc.sessionMutationPending
    ) return;
    this.createError = '';
    this.joinError = '';
    this.view = this.view === 'catalog' ? 'home' : 'catalog';
  }

  openCreate(): void {
    if (this.creating || this.joining || this.sessionActionPending || this.svc.sessionMutationPending) return;
    this.createError = '';
    this.joinError = '';
    this.view = this.view === 'create' ? 'home' : 'create';
  }

  openJoin(): void {
    if (this.creating || this.joining || this.sessionActionPending || this.svc.sessionMutationPending) return;
    this.createError = '';
    this.joinError = '';
    this.view = this.view === 'join' ? 'home' : 'join';
  }

  closeSubView(): void {
    this.createError = '';
    this.joinError = '';
    this.view = 'home';
  }

  onSessionSwitched(): void {
    this.openActiveSessionChat();
  }

  onSessionOpened(): void {
    this.openActiveSessionChat();
  }

  private openActiveSessionChat(): void {
    this.createError = '';
    this.joinError = '';
    this.sessionActionError = '';
    this.activeTab = 'chat';
    this.view = 'home';
  }

  quickCompactShareLabel(): string {
    if (this.creating) return 'Starte…';
    if (!this.svc.isActive) return '⚡ Ananta-App schnell teilen';
    if (this.pairSync.isLocalCompactSharingPending) return '⚡ Schnellteilen abbrechen';
    if (this.pairSync.isLocalViewSharingEnabled || this.pairSync.isLocalCursorSharingEnabled) {
      return '⚡ Ananta-App nicht mehr teilen';
    }
    if (!this.activeSessionSupportsCompactShare()) return '⚡ Neue Schnellteilen-Session';
    return '⚡ Ananta-App schnell teilen';
  }

  quickCompactShareDisabled(): boolean {
    if (this.creating || this.joining || this.sessionActionPending || this.svc.sessionMutationPending) return true;
    if (!this.svc.isActive) return false;
    if (
      this.pairSync.isLocalCompactSharingPending
      || this.pairSync.isLocalViewSharingEnabled
      || this.pairSync.isLocalCursorSharingEnabled
    ) return false;
    if (
      this.activeSessionSupportsCompactShare()
      && this.svc.state$.value.role === 'participant'
      && !this.svc.state$.value.session?.security_epoch
    ) return true;
    return false;
  }

  quickCompactShareTitle(): string {
    if (!this.svc.isActive || this.activeSessionSupportsCompactShare()) return '';
    return 'Erstellt eine Schnellteilen-Session; die aktuelle Session bleibt geparkt erhalten.';
  }

  activeSessionSupportsCompactShare(): boolean {
    const session = this.svc.state$.value.session;
    return session?.permissions?.['view_tui'] === true
      && session.permissions?.['remote_cursor'] === true;
  }

  private async createQuickCompactSession(): Promise<void> {
    this.creating = true;
    this.createError = '';
    try {
      const session = await this.svc.createSession('Ananta-App gemeinsam ansehen', {
        chat: true,
        view_tui: true,
        remote_cursor: true,
        artifact_share: false,
        remote_control: false,
      }, 3600, { expectedAuthority: 'public' });
      if (session?.id) this.pairSync.armLocalCompactSharingOnFirstPeerReady(session.id);
      this.view = 'home';
      this.activeTab = 'chat';
    } catch (error: unknown) {
      this.createError = pairSessionErrorMessage(error, 'Schnellteilen fehlgeschlagen');
    } finally {
      this.creating = false;
    }
  }

  async sendMsg(): Promise<void> {
    if (!this.chatInput.trim()) return;
    this.chatError = '';
    const value = this.chatInput.trim();
    try {
      await this.svc.sendMessage(value);
      this.chatInput = '';
    } catch (error) {
      this.chatError = error instanceof Error ? error.message : 'Nachricht konnte nicht sicher gesendet werden';
    }
  }

  async doEnd(): Promise<void> {
    if (this.sessionActionPending) return;
    if (!confirm('Session wirklich beenden? Alle Teilnehmer werden getrennt.')) return;
    await this.runSessionAction(
      () => this.svc.endSession(),
      'Session konnte nicht sicher beendet werden',
    );
  }

  async doLeave(): Promise<void> {
    if (this.sessionActionPending) return;
    await this.runSessionAction(
      () => this.svc.leaveSession(),
      'Session konnte nicht sicher verlassen werden',
    );
  }

  revoke(p: ShareParticipant): void {
    if (!confirm(`Teilnehmer "${p.user_id || p.device_id}" sperren?`)) return;
    this.svc.revokeParticipant(p.id);
  }

  canChat(state: any): boolean {
    const permitted = state?.role === 'owner' || !!state?.session?.permissions?.chat;
    return permitted && this.svc.canSendChat();
  }

  private async runSessionAction(
    action: () => Promise<void>,
    fallbackMessage: string,
  ): Promise<void> {
    if (this.sessionActionPending) return;
    this.sessionActionPending = true;
    this.sessionActionError = '';
    try {
      await action();
      this.view = 'home';
    } catch (error) {
      this.sessionActionError = pairSessionErrorMessage(error, fallbackMessage);
      // A definitive server rejection may retire the local session before the
      // awaited action rejects. Return to the only inactive view that renders
      // the contextual failure instead of leaving it hidden behind the former
      // create/join sub-view.
      if (!this.svc.isActive) this.view = 'home';
    } finally {
      this.sessionActionPending = false;
    }
  }

  securityLabel(state: PairSecurityBootstrapState): string {
    return pairSecurityLabel(state);
  }

  securityFingerprint(state: PairSecurityBootstrapState): string {
    return pairSecurityFingerprint(state);
  }

  isOwnMessage(senderId: string): boolean {
    const uid = this.svc.currentUserId;
    return !!uid && senderId === uid;
  }

  permEntries(perms: Record<string, boolean>): Array<{ key: string; val: boolean }> {
    return permissionEntries(perms);
  }

  remoteViewEntries(views: ReadonlyMap<string, RemoteViewProjection>): RemoteViewEntry[] {
    return toRemoteViewEntries(views);
  }

  toggleOwnCompactView(sessionId: string, securityEpoch: number): void {
    this.pairSync.setLocalCompactSharing(sessionId, securityEpoch, {
      view: !this.pairSync.isLocalViewSharingEnabled,
      cursor: this.pairSync.isLocalCursorSharingEnabled,
    });
  }

  toggleOwnCompactCursor(sessionId: string, securityEpoch: number): void {
    this.pairSync.setLocalCompactSharing(sessionId, securityEpoch, {
      view: this.pairSync.isLocalViewSharingEnabled,
      cursor: !this.pairSync.isLocalCursorSharingEnabled,
    });
  }

  cancelPendingCompactShare(sessionId: string): void {
    this.pairSync.cancelPendingLocalCompactSharing(sessionId);
  }

  async copyCode(code: string): Promise<void> {
    if (!code) return;
    await navigator.clipboard.writeText(code).catch(() => {});
  }
}

