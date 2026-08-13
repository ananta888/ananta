import { CommonModule } from '@angular/common';
import { Component, EventEmitter, OnDestroy, OnInit, Output, inject } from '@angular/core';

import {
  ShareSessionCatalogEntry,
  ShareSessionService,
} from '../services/share-session.service';
import { pairSessionErrorMessage } from '../services/pair-session-error-message';

/**
 * Read-only catalogue plus an explicit active-session selector.
 *
 * The component never ends or leaves a membership. Runtime parking and
 * activation stay behind ShareSessionService so the UI cannot accidentally
 * turn a visual selection into a destructive lifecycle operation (SRP/DIP).
 */
@Component({
  selector: 'app-pair-session-catalog',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="catalog" data-testid="pair-session-catalog" aria-labelledby="pair-session-catalog-title">
      <div class="catalog-head">
        <div>
          <strong id="pair-session-catalog-title">Meine Pair-Sessions</strong>
          <small>
            Nur eine Session ist lokal verbunden. Geparkte Sessions bleiben in diesem Browser-Tab sicher wiederverbindbar.
          </small>
        </div>
        <button type="button" class="catalog-button" (click)="load()" [disabled]="loading || !!switchingSessionId">
          {{ loading ? 'Lade…' : 'Aktualisieren' }}
        </button>
      </div>
      <p class="catalog-explainer">
        Keycloak bestätigt dein Konto; webrtc.ananta.de findet deine bestehenden Mitgliedschaften.
        Neue Gegenüber treten weiterhin mit dem Einladungscode bei.
      </p>

      @if (error) {
        <div class="catalog-error" role="alert">{{ error }}</div>
      }

      @if (!loading && !entries.length && !error) {
        <div class="catalog-empty">Noch keine wiederverwendbare Pair-Session vorhanden.</div>
      }

      <div class="catalog-list">
        @for (entry of orderedEntries; track entry.session.id) {
          @let active = isActive(entry);
          @let selected = isSelected(entry);
          <article
            class="catalog-row"
            [class.active]="active"
            [attr.data-session-state]="entry.session.local_runtime_state || (active ? 'active' : 'parked')">
            <div class="catalog-main">
              <span class="catalog-title">{{ entry.session.title || 'Pair-Session' }}</span>
              <span class="catalog-meta">
                {{ roleLabel(entry) }} · {{ peerLabel(entry) }} · {{ runtimeLabel(entry) }}
              </span>
              @if (entry.session.expires_at) {
                <span class="catalog-meta">Gültig bis {{ entry.session.expires_at * 1000 | date:'short' }}</span>
              }
            </div>
            @if (active) {
              <span class="catalog-state" data-testid="active-pair-session">Aktiv</span>
            } @else {
              <button
                type="button"
                class="catalog-button switch"
                data-testid="switch-pair-session"
                (click)="switchTo(entry)"
                [disabled]="loading || !!switchingSessionId">
                {{ switchingSessionId === entry.session.id
                  ? 'Verbinde…'
                  : selected ? 'Wieder aktivieren' : 'Wechseln' }}
              </button>
            }
          </article>
        }
      </div>
    </section>
  `,
  styles: [`
    :host { display: block; min-height: 0; }
    .catalog { display: grid; gap: 8px; padding: 9px 10px; }
    .catalog-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
    .catalog-head > div, .catalog-main { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
    .catalog-head strong { color: #a8c7ff; }
    .catalog-head small, .catalog-meta { color: #6b8ab8; font-size: 10px; line-height: 1.35; }
    .catalog-explainer { margin: 0; color: #7f9bbd; font-size: 10px; line-height: 1.4; }
    .catalog-list { display: grid; gap: 6px; overflow-y: auto; }
    .catalog-row {
      display: flex; align-items: center; gap: 8px; padding: 7px;
      border: 1px solid #1a2d4a; border-radius: 3px; background: #0d1828;
    }
    .catalog-row.active { border-color: #28745e; background: #102238; }
    .catalog-main { flex: 1; }
    .catalog-title { overflow: hidden; color: #c8d8f8; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
    .catalog-state { color: #7fffd4; font-size: 10px; }
    .catalog-button {
      flex-shrink: 0; border: 1px solid #2a4070; border-radius: 3px; padding: 4px 7px;
      background: #162444; color: #a8c7ff; cursor: pointer; font: inherit; font-size: 10px;
    }
    .catalog-button:hover:not([disabled]) { border-color: #7fffd4; color: #7fffd4; }
    .catalog-button[disabled] { cursor: not-allowed; opacity: .45; }
    .catalog-error { color: #fb7185; font-size: 11px; }
    .catalog-empty { color: #4a6a9a; font-size: 11px; padding: 7px 0; }
  `],
})
export class PairSessionCatalogComponent implements OnInit, OnDestroy {
  private readonly sessions = inject(ShareSessionService);

  @Output() readonly switched = new EventEmitter<string>();

  entries: readonly ShareSessionCatalogEntry[] = [];
  loading = false;
  switchingSessionId = '';
  error = '';
  private initialLoadHandle: ReturnType<typeof setTimeout> | null = null;

  ngOnInit(): void {
    // Do not mutate the toolbar's disabled state from inside the initial
    // Angular check. The queued refresh also coalesces naturally with the
    // panel becoming visible in the same navigation tick.
    this.initialLoadHandle = setTimeout(() => {
      this.initialLoadHandle = null;
      void this.load();
    }, 0);
  }

  ngOnDestroy(): void {
    if (this.initialLoadHandle !== null) clearTimeout(this.initialLoadHandle);
    this.initialLoadHandle = null;
  }

  get orderedEntries(): readonly ShareSessionCatalogEntry[] {
    const activeId = this.sessions.state$.value.session?.id ?? '';
    return [...this.entries].sort((left, right) => {
      if (left.session.id === activeId) return -1;
      if (right.session.id === activeId) return 1;
      return Number(right.session.created_at || 0) - Number(left.session.created_at || 0);
    });
  }

  async load(): Promise<void> {
    if (this.initialLoadHandle !== null) {
      clearTimeout(this.initialLoadHandle);
      this.initialLoadHandle = null;
    }
    if (this.loading || this.switchingSessionId) return;
    this.loading = true;
    this.error = '';
    try {
      this.entries = await this.sessions.listSessions({ expectedAuthority: 'public' });
    } catch (error: unknown) {
      this.entries = [];
      this.error = pairSessionErrorMessage(error, 'Pair-Sessions konnten nicht geladen werden');
    } finally {
      this.loading = false;
    }
  }

  async switchTo(entry: ShareSessionCatalogEntry): Promise<void> {
    const sessionId = entry.session.id;
    if (!sessionId || this.isActive(entry) || this.switchingSessionId) return;
    this.switchingSessionId = sessionId;
    this.error = '';
    try {
      await this.sessions.switchToSession(sessionId, { expectedAuthority: 'public' });
      const activeSession = this.sessions.state$.value.session;
      this.entries = this.entries.map(item => ({
        ...item,
        session: {
          ...item.session,
          ...(item.session.id === sessionId && activeSession?.id === sessionId
            ? activeSession
            : { local_runtime_state: 'parked' as const }),
        },
      }));
      this.switched.emit(sessionId);
    } catch (error: unknown) {
      this.error = pairSessionErrorMessage(error, 'Sessionwechsel fehlgeschlagen');
    } finally {
      this.switchingSessionId = '';
    }
  }

  isActive(entry: ShareSessionCatalogEntry): boolean {
    return this.isSelected(entry) && entry.session.local_runtime_state !== 'parked';
  }

  isSelected(entry: ShareSessionCatalogEntry): boolean {
    return this.sessions.state$.value.session?.id === entry.session.id;
  }

  runtimeLabel(entry: ShareSessionCatalogEntry): string {
    if (this.isActive(entry)) return 'aktiv verbunden';
    if (this.isSelected(entry)) return 'lokal ausgewählt · serverseitig geparkt';
    return entry.session.local_runtime_state === 'active'
      ? 'in einem anderen Tab aktiv'
      : 'geparkt';
  }

  roleLabel(entry: ShareSessionCatalogEntry): string {
    return entry.role === 'owner' ? 'Eigentümer' : 'Teilnehmer';
  }

  peerLabel(entry: ShareSessionCatalogEntry): string {
    const session = entry.session;
    const serverLabel = String(session.peer_label || '').trim();
    if (serverLabel) return serverLabel;
    // Legacy/Hub rows may not yet provide the privacy-preserving catalog
    // label. Public identity-v2 rows deliberately never derive one from raw
    // peer/device identifiers in the browser.
    if (session.identity_binding_version === 2) {
      return Number(session.participant_count || 0) > 0
        ? 'Gegenüber verbunden'
        : 'Noch kein Gegenüber';
    }
    const localPeerId = String(session.local_peer_id || '');
    const participants = Array.isArray(session.participants) ? session.participants : [];
    const remoteParticipant = participants.find(participant => {
      const peerId = String(participant.peer_id || participant.user_id || participant.device_id || '');
      return peerId && peerId !== localPeerId;
    });
    const remotePeerId = String(
      remoteParticipant?.peer_id
      || remoteParticipant?.user_id
      || remoteParticipant?.device_id
      || (entry.role === 'participant' ? session.owner_peer_id : '')
      || '',
    );
    return remotePeerId ? localPeerLabel(remotePeerId) : 'Noch kein Gegenüber';
  }
}

function localPeerLabel(peerId: string): string {
  let hash = 0;
  for (let index = 0; index < peerId.length; index += 1) {
    hash = (Math.imul(hash, 31) + peerId.charCodeAt(index)) >>> 0;
  }
  return `Peer-${hash.toString(16).padStart(8, '0').slice(0, 6)}`;
}
