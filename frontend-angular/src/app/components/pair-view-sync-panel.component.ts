/**
 * T09 / T10: PairViewSyncPanelComponent
 *
 * A small, single-component dialog for the Pair-Dev view-sync.
 * Two roles:
 *  - Owner: choose permissions, create a session, see who joined.
 *  - Participant: see what the owner is currently showing, toggle
 *    follow mode, request control when allowed.
 *
 * The component is a thin shell around the services; all the
 * real work happens in SharedViewStateService, ViewDeltaService
 * and PairViewSyncService.
 */
import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, inject, signal } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';

import { ShareSessionService, ShareParticipant, ShareSession } from '../services/share-session.service';
import { SharedViewStateService } from '../services/shared-view-state.service';
import { PairViewSyncService } from '../services/pair-view-sync.service';
import { PERMISSION_LABELS, permissionsFromUiSelection } from '../services/permission-labels';
import { PermissionKey, SharedViewState } from '../services/pair-view-sync.types';

interface CreateFormState {
  title: string;
  expiresInMinutes: number;
  selected: Record<PermissionKey, boolean>;
}

@Component({
  selector: 'app-pair-view-sync-panel',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  <section class="pair-panel" role="dialog" aria-label="Pair-Dev View-Sync">
    <header>
      <h3>Pair-Dev View-Sync</h3>
      <span class="mode-tag" [class.active]="activeSession() !== null">
        {{ activeSession() ? 'aktiv' : 'inaktiv' }}
      </span>
    </header>

    @if (activeSession() === null) {
      <form (submit)="$event.preventDefault(); onCreate()">
        <label class="title-row">
          <span>Titel</span>
          <input type="text" name="title" [(ngModel)]="form.title" maxlength="80" required />
        </label>
        <label class="expire-row">
          <span>läuft ab in</span>
          <select name="exp" [(ngModel)]="form.expiresInMinutes">
            <option [ngValue]="15">15 min</option>
            <option [ngValue]="60">1 h</option>
            <option [ngValue]="480">8 h</option>
            <option [ngValue]="null">nie</option>
          </select>
        </label>
        <fieldset class="permissions">
          <legend>Berechtigungen</legend>
          @for (entry of permissionEntries; track entry.key) {
            <label class="perm-row">
              <input type="checkbox" [(ngModel)]="form.selected[entry.key]" [name]="entry.key" />
              <span class="perm-label">{{ entry.label }}</span>
              <span class="perm-desc">{{ entry.description }}</span>
              @if (entry.requiresExplicitGrant) {
                <span class="grant-tag">explizit</span>
              }
            </label>
          }
        </fieldset>
        <div class="error" role="alert" [hidden]="!error()">{{ error() }}</div>
        <button type="submit" [disabled]="!form.title.trim() || busy()">
          {{ busy() ? 'erstelle…' : 'Session erstellen' }}
        </button>
      </form>
    } @else {
      <div class="session-info">
        <strong>{{ activeSession()!.title }}</strong>
        <code>Einladung: {{ activeSession()!.invite_code }}</code>
        <span class="role-tag" [class.owner]="role() === 'owner'">
          Rolle: {{ role() === 'owner' ? 'Owner' : 'Teilnehmer' }}
        </span>
      </div>

      <div class="peer-state" [class.empty]="!peerState()">
        @if (peerState()) {
          <small>Peer-Ansicht:</small>
          <code>/{{ peerState()!.activeSurface }} / {{ peerState()!.activeTab || '–' }}</code>
          <small>seq #{{ peerState()!.seq }} · hash {{ peerState()!.viewHash }}</small>
        } @else {
          <small>Noch keine Peer-Aktualisierung erhalten.</small>
        }
      </div>

      <div class="follow-row">
        <button type="button" (click)="toggleFollow()">
          {{ sync.getFollowMode() === 'active' ? 'Follow pausieren' : 'Follow fortsetzen' }}
        </button>
        @if (role() !== 'owner' && share.hasPermission('control')) {
          <button type="button" (click)="onRequestControl()" [disabled]="sync.hasControlGrant()">
            {{ sync.hasControlGrant() ? 'Steuerung erhalten' : 'Steuerung anfragen' }}
          </button>
        }
        @if (role() === 'owner' && !sync.hasControlGrant()) {
          <span class="hint">T12: Steuerung wartet auf Anfrage.</span>
        }
      </div>

      <div class="participants">
        <small>Teilnehmer: {{ participants().length }}</small>
        @for (p of participants(); track p.id) {
          <div class="participant">
            <span class="status" [class.online]="share.participantStatus(p) === 'online'"></span>
            <code>{{ p.user_id }}</code>
            <span class="status-text">{{ share.participantStatus(p) }}</span>
          </div>
        }
      </div>

      <div class="end-row">
        <button type="button" class="danger" (click)="onEnd()" [disabled]="busy()">
          {{ role() === 'owner' ? 'Session beenden' : 'Session verlassen' }}
        </button>
      </div>
    }
  </section>
  `,
  styles: [`
    :host { display: block; font-family: inherit; }
    .pair-panel {
      background: var(--panel-bg, #1e1e22);
      color: var(--panel-fg, #f3f3f5);
      border-radius: 10px;
      padding: 1rem 1.2rem;
      max-width: 480px;
      box-shadow: 0 6px 24px rgba(0,0,0,0.35);
    }
    header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.8rem; }
    h3 { margin: 0; font-size: 1.05rem; font-weight: 600; }
    .mode-tag {
      font-size: 0.7rem; padding: 0.15rem 0.5rem; border-radius: 999px;
      background: #3a3a3f; color: #bbb;
    }
    .mode-tag.active { background: #2b8a3e; color: white; }
    label.title-row, label.expire-row {
      display: flex; flex-direction: column; margin-bottom: 0.6rem; gap: 0.25rem;
    }
    label.title-row > span, label.expire-row > span { font-size: 0.78rem; color: #b9b9c2; }
    input[type=text], select {
      background: #2a2a2f; color: inherit; border: 1px solid #3a3a3f;
      border-radius: 6px; padding: 0.4rem 0.55rem; font: inherit;
    }
    fieldset.permissions { border: 1px solid #2f2f34; border-radius: 8px; padding: 0.5rem 0.75rem; margin: 0 0 0.8rem; }
    legend { font-size: 0.8rem; color: #b9b9c2; padding: 0 0.4rem; }
    .perm-row {
      display: grid; grid-template-columns: auto 7rem 1fr auto; gap: 0.45rem;
      align-items: center; padding: 0.3rem 0; font-size: 0.85rem;
    }
    .perm-label { font-weight: 600; }
    .perm-desc { color: #a4a4ad; font-size: 0.78rem; }
    .grant-tag {
      font-size: 0.65rem; padding: 0.1rem 0.4rem; border-radius: 999px;
      background: #5a3a14; color: #ffd9a8; text-transform: uppercase; letter-spacing: 0.05em;
    }
    button {
      background: #4263eb; color: white; border: 0; border-radius: 6px;
      padding: 0.55rem 0.9rem; font: inherit; cursor: pointer;
    }
    button:disabled { background: #2a2a30; color: #777; cursor: not-allowed; }
    button.danger { background: #c92a2a; }
    .error { color: #ff8787; font-size: 0.8rem; margin: 0.4rem 0; }
    .session-info { display: flex; flex-direction: column; gap: 0.3rem; margin-bottom: 0.7rem; }
    .role-tag {
      align-self: flex-start; font-size: 0.7rem; padding: 0.1rem 0.45rem;
      border-radius: 999px; background: #2a2a30; color: #b9b9c2;
    }
    .role-tag.owner { background: #1864ab; color: white; }
    .peer-state {
      background: #232328; padding: 0.5rem 0.6rem; border-radius: 6px;
      display: flex; flex-direction: column; gap: 0.2rem; margin-bottom: 0.6rem;
      font-size: 0.8rem;
    }
    .peer-state.empty { color: #888; }
    .follow-row { display: flex; gap: 0.4rem; align-items: center; margin-bottom: 0.6rem; flex-wrap: wrap; }
    .hint { color: #888; font-size: 0.75rem; }
    .participants { display: flex; flex-direction: column; gap: 0.25rem; margin-bottom: 0.7rem; }
    .participant { display: flex; align-items: center; gap: 0.4rem; font-size: 0.8rem; }
    .status {
      width: 0.5rem; height: 0.5rem; border-radius: 50%; background: #555;
    }
    .status.online { background: #51cf66; }
    .status-text { color: #a4a4ad; font-size: 0.7rem; }
  `],
})
export class PairViewSyncPanelComponent implements OnInit, OnDestroy {
  share = inject(ShareSessionService);
  sync = inject(PairViewSyncService);
  private view = inject(SharedViewStateService);

  readonly permissionEntries = Object.values(PERMISSION_LABELS);
  readonly activeSession = signal<ShareSession | null>(null);
  readonly participants = signal<ShareParticipant[]>([]);
  readonly role = signal<'owner' | 'participant' | null>(null);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly peerState = signal<SharedViewState | null>(null);

  form: CreateFormState = {
    title: '',
    expiresInMinutes: 60,
    selected: {
      chat: true, view_tui: true, cursor: false,
      control: false, artifact_view: true, annotation: false,
    },
  };

  private subs: Subscription[] = [];

  ngOnInit(): void {
    this.subs.push(this.share.state$.subscribe((s) => {
      this.activeSession.set(s.session);
      this.participants.set(s.participants);
      this.role.set(s.role);
    }));
    this.subs.push(this.view.state$.subscribe((s) => {
      // Peer state = whichever side is NOT us. For a true bidirectional
      // sync we'd also subscribe to inbound states; for v1 we just show
      // our local state when we're a participant.
      if (this.role() === 'participant') this.peerState.set(s);
    }));
  }

  async onCreate(): Promise<void> {
    if (!this.form.title.trim()) return;
    this.busy.set(true);
    this.error.set('');
    try {
      const perms = permissionsFromUiSelection(this.form.selected);
      const expires = this.form.expiresInMinutes === null
        ? null
        : this.form.expiresInMinutes * 60;
      const session = await this.share.createSession(this.form.title, perms, expires);
      this.sync.bindSession(
        session.id,
        this.share.currentUserId || 'owner',
      );
    } catch (e: unknown) {
      this.error.set(e instanceof Error ? e.message : 'unbekannter Fehler');
    } finally {
      this.busy.set(false);
    }
  }

  toggleFollow(): void {
    this.sync.setFollowMode(this.sync.getFollowMode() === 'active' ? 'paused' : 'active');
  }

  onRequestControl(): void {
    // T12: route through PairViewSyncService so the service
    // can enforce session-state checks and the transport
    // envelope stays consistent with the rest of the protocol.
    this.sync.requestControl();
  }

  onEnd(): void {
    if (this.role() === 'owner') {
      this.share.endSession();
    } else {
      this.share.leaveSession();
    }
    this.sync.unbindSession();
  }

  ngOnDestroy(): void {
    for (const s of this.subs) s.unsubscribe();
  }
}
