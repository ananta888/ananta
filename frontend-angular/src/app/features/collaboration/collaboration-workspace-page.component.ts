import { JsonPipe } from '@angular/common';
import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PageIntroComponent, SectionCardComponent } from '../../shared/ui/layout';
import { CollaborationLivePanelComponent } from './collaboration-live-panel.component';
import { CollaborationWorkspaceApiService } from './collaboration-workspace-api.service';
import {
  CollaborationEvent,
  CollaborationFlowProjection,
  CollaborationMembership,
  CollaborationPresence,
  CollaborationRoom,
  CollaborationThread,
  CollaborationWorkspaceSummary,
} from './collaboration-workspace.models';

@Component({
  selector: 'app-collaboration-workspace-page',
  standalone: true,
  imports: [FormsModule, JsonPipe, PageIntroComponent, SectionCardComponent, CollaborationLivePanelComponent],
  template: `
    <app-page-intro
      title="Collaboration Workspaces"
      subtitle="Dauerhafte Hub-autorisierte Rooms und Timeline; Live-Transport und externe Bridges bleiben getrennte Adapter." />
    <div class="grid">
      <app-section-card title="Workspaces" subtitle="Native Core funktioniert ohne Buzz und ohne menschliche Freigabe.">
        <div class="row">
          <input [(ngModel)]="newTitle" maxlength="200" placeholder="Workspace-Titel" />
          <button type="button" (click)="createWorkspace()" [disabled]="busy || !newTitle.trim()">Erstellen</button>
        </div>
        @for (workspace of workspaces; track workspace.workspace_id) {
          <button type="button" class="entry" (click)="open(workspace)">{{ workspace.title }}</button>
        }
      </app-section-card>
      <app-section-card title="Rooms und Timeline" subtitle="Append-only, replaybar und tenant-isoliert.">
        @if (selected) {
          <div class="row">
            <input [(ngModel)]="newRoomTitle" maxlength="200" placeholder="Room-Titel" />
            <button type="button" (click)="createRoom()" [disabled]="busy || !newRoomTitle.trim()">Room anlegen</button>
          </div>
          <div class="rooms">
            @for (room of rooms; track room.room_id) {
              <button type="button" class="entry" (click)="openRoom(room)">
                <strong>{{ room.title }}</strong>
                <small>{{ room.room_kind }} · {{ room.access_mode || 'workspace' }} · {{ room.lifecycle_state || 'active' }}</small>
              </button>
            }
          </div>
          @if (selectedRoom) {
            <div class="room-meta">
              <span>Binding: {{ selectedRoom.binding_kind || 'frei' }} / {{ selectedRoom.binding_id || '—' }}</span>
              <span>Sichtbarkeit: {{ selectedRoom.access_mode || 'workspace' }}</span>
              <span>Bridge: optional / nicht erforderlich</span>
              <button type="button" (click)="toggleRoomLifecycle()" [disabled]="busy">
                {{ selectedRoom.lifecycle_state === 'archived' ? 'Wieder öffnen' : 'Archivieren' }}
              </button>
            </div>
            <div class="timeline">
              @for (event of events; track event.event_id) {
                <article>
                  <strong>{{ event.event_type }} · {{ event.actor_binding_id }}</strong>
                  <span>{{ event.payload['text'] || (event.payload | json) }}</span>
                  @if (event.payload_digest) { <small>Digest: {{ event.payload_digest }}</small> }
                  @if (event.event_type === 'message.posted') {
                    <button type="button" (click)="openThread(event)">Thread öffnen</button>
                  }
                </article>
              }
            </div>
            <div class="row">
              <input [(ngModel)]="message" maxlength="4000" placeholder="Nachricht" />
              <button type="button" (click)="send()" [disabled]="busy || !message.trim() || selectedRoom.lifecycle_state === 'archived'">Senden</button>
            </div>
            @if (selectedThread) {
              <section class="thread" aria-label="Thread">
                <h3>Thread · {{ selectedThread.status }} · Revision {{ selectedThread.revision }}</h3>
                @for (event of selectedThread.events; track event.event_id) {
                  <p><strong>{{ event.actor_binding_id }}</strong>: {{ event.payload['text'] || event.event_type }}</p>
                }
                <div class="row">
                  <input [(ngModel)]="reply" maxlength="4000" placeholder="Antwort" />
                  <button type="button" (click)="sendReply()" [disabled]="busy || !reply.trim() || selectedThread.status !== 'open'">Antworten</button>
                </div>
              </section>
            }
            <div class="row">
              <input [(ngModel)]="searchQuery" maxlength="128" placeholder="Workspace durchsuchen" />
              <button type="button" (click)="search()" [disabled]="busy || searchQuery.trim().length < 2">Suchen</button>
            </div>
            @for (result of searchResults; track result.event_id) {
              <p class="search-result">{{ result.event_type }} · {{ result.payload['text'] || (result.payload | json) }}</p>
            }
          }
        } @else {
          <p>Workspace auswählen oder neu erstellen.</p>
        }
        @if (error) { <p class="error" role="alert">{{ error }}</p> }
      </app-section-card>
      <app-section-card title="Actors und Rechte" subtitle="Presence ist Live-Zustand und verleiht weder Membership noch Task-Autorität.">
        <app-collaboration-live-panel
          [workspace]="selected"
          [room]="selectedRoom"
          [memberships]="memberships"
          [presence]="presence" />
        @if (selectedRoom) {
          <button type="button" (click)="loadPresence()" [disabled]="busy">Presence aktualisieren</button>
        }
      </app-section-card>
      <app-section-card title="Fachprojektionen" subtitle="Nur lesbare Hub-Projektionen; Änderungen laufen über getrennte Intent-Pfade.">
        @if (flow) {
          <small>Checkpoint {{ flow.checkpoint }} · Digest {{ flow.state_digest }}</small>
          @for (task of projectionEntries(flow.state.tasks); track task[0]) {
            <article><strong>Task · {{ task[0] }}</strong><span>{{ task[1] | json }}</span></article>
          }
          @for (workflow of projectionEntries(flow.state.workflows); track workflow[0]) {
            <article><strong>Workflow · {{ workflow[0] }}</strong><span>{{ workflow[1] | json }}</span></article>
          }
          @for (ref of projectionEntries(flow.state.git_refs); track ref[0]) {
            <article><strong>Git · {{ ref[0] }}</strong><span>{{ ref[1] | json }}</span></article>
          }
          @for (review of projectionEntries(flow.state.reviews); track review[0]) {
            <article><strong>Review/Artifact · {{ review[0] }}</strong><span>{{ review[1] | json }}</span></article>
          }
          @for (artifact of projectionEntries(flow.state.artifacts); track artifact[0]) {
            <article><strong>Artifact · {{ artifact[0] }}</strong><span>{{ artifact[1] | json }}</span></article>
          }
          @if (!hasProjectionEntries()) { <p>Noch keine belegten Fachprojektionen.</p> }
        } @else {
          <p>Workspace auswählen, um permission-gefilterte Projektionen zu laden.</p>
        }
      </app-section-card>
    </div>
  `,
  styles: [`
    .grid { display:grid; grid-template-columns:minmax(240px,1fr) minmax(320px,2fr); gap:16px; }
    .row,.rooms { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
    input { min-width:180px; flex:1; }
    .entry { display:grid; gap:3px; width:100%; margin:6px 0; text-align:left; }
    .timeline { display:grid; gap:8px; max-height:420px; overflow:auto; margin:12px 0; }
    article { display:grid; gap:4px; padding:8px; border:1px solid var(--border-color); border-radius:6px; }
    .room-meta,.thread { display:grid; gap:6px; margin:12px 0; padding:10px; border:1px solid var(--border-color); border-radius:6px; }
    .search-result { padding:6px; border-inline-start:3px solid var(--primary-color); }
    .error { color:var(--danger); }
    @media(max-width:760px){.grid{grid-template-columns:1fr;}}
  `],
})
export class CollaborationWorkspacePageComponent implements OnInit {
  private readonly api = inject(CollaborationWorkspaceApiService);
  private readonly agents = inject(AgentDirectoryService);
  private readonly changeDetector = inject(ChangeDetectorRef);
  workspaces: CollaborationWorkspaceSummary[] = [];
  rooms: CollaborationRoom[] = [];
  events: CollaborationEvent[] = [];
  memberships: CollaborationMembership[] = [];
  presence: CollaborationPresence[] = [];
  flow: CollaborationFlowProjection | null = null;
  searchResults: CollaborationEvent[] = [];
  selected: CollaborationWorkspaceSummary | null = null;
  selectedRoom: CollaborationRoom | null = null;
  selectedThread: CollaborationThread | null = null;
  newTitle = '';
  newRoomTitle = '';
  message = '';
  reply = '';
  searchQuery = '';
  busy = false;
  error = '';

  ngOnInit(): void { this.load(); }

  load(): void {
    const hub = this.hubUrl();
    if (!hub) { this.error = 'Kein Hub konfiguriert.'; return; }
    this.api.list(hub).subscribe({
      next: page => { this.workspaces = page.items; this.render(); },
      error: error => this.fail(error),
    });
  }

  createWorkspace(): void {
    const hub = this.hubUrl();
    if (!hub || !this.newTitle.trim()) return;
    this.busy = true;
    this.api.create(hub, this.newTitle.trim()).subscribe({
      next: workspace => {
        this.busy = false;
        this.newTitle = '';
        this.workspaces = [...this.workspaces, workspace];
        this.open(workspace);
        this.render();
      },
      error: error => this.fail(error),
    });
  }

  open(workspace: CollaborationWorkspaceSummary): void {
    const hub = this.hubUrl();
    if (!hub) return;
    this.selected = workspace;
    this.selectedRoom = null;
    this.selectedThread = null;
    this.presence = [];
    this.flow = null;
    this.events = [];
    this.api.get(hub, workspace.workspace_id).subscribe({
      next: detail => {
        this.rooms = detail.rooms;
        this.memberships = detail.memberships;
        this.loadFlowProjection();
        this.render();
      },
      error: error => this.fail(error),
    });
  }

  createRoom(): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected || !this.newRoomTitle.trim()) return;
    const room: CollaborationRoom = {
      schema: 'ananta.collaboration-room.v1',
      room_id: `room-${crypto.randomUUID()}`,
      room_kind: 'freeform',
      title: this.newRoomTitle.trim(),
      binding_kind: null,
      binding_id: null,
    };
    this.api.createRoom(hub, this.selected.workspace_id, room).subscribe({
      next: created => {
        this.newRoomTitle = '';
        this.rooms = [...this.rooms, created];
        this.openRoom(created);
        this.render();
      },
      error: error => this.fail(error),
    });
  }

  openRoom(room: CollaborationRoom): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected) return;
    this.selectedRoom = room;
    this.selectedThread = null;
    this.api.timeline(hub, this.selected.workspace_id, room.room_id).subscribe({
      next: page => { this.events = page.items; this.loadPresence(); this.render(); },
      error: error => this.fail(error),
    });
  }

  async send(): Promise<void> {
    const hub = this.hubUrl();
    if (!hub || !this.selected || !this.selectedRoom || !this.message.trim()) return;
    this.busy = true;
    const payload = { text: this.message.trim() };
    const event = {
      schema: 'ananta.workspace-event.v1',
      event_id: `event-${crypto.randomUUID()}`,
      workspace_id: this.selected.workspace_id,
      room_id: this.selectedRoom.room_id,
      thread_id: null,
      event_type: 'message.posted',
      actor_binding_id: this.selected.current_actor_binding_id || this.selected.created_by,
      idempotency_key: `message-${crypto.randomUUID()}`,
      correlation_id: `correlation-${crypto.randomUUID()}`,
      causation_id: null,
      visibility: 'room',
      retention: 'standard',
      occurred_at: Date.now() / 1000,
      payload,
      payload_digest: await sha256(canonicalJson(payload)),
      source_refs: [],
      run_refs: [],
    };
    this.api.append(hub, this.selected.workspace_id, event).subscribe({
      next: appended => {
        this.busy = false;
        this.message = '';
        this.events = [...this.events, appended];
        this.render();
      },
      error: error => this.fail(error),
    });
  }

  openThread(event: CollaborationEvent): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected) return;
    this.api.thread(hub, this.selected.workspace_id, event.event_id).subscribe({
      next: thread => { this.selectedThread = thread; this.render(); },
      error: error => this.fail(error),
    });
  }

  async sendReply(): Promise<void> {
    const hub = this.hubUrl();
    if (!hub || !this.selected || !this.selectedRoom || !this.selectedThread || !this.reply.trim()) return;
    this.busy = true;
    const payload = { text: this.reply.trim(), expected_thread_revision: this.selectedThread.revision };
    const event = await this.eventEnvelope('message.replied', payload, this.selectedThread.thread_id);
    this.api.append(hub, this.selected.workspace_id, event).subscribe({
      next: appended => {
        this.busy = false;
        this.reply = '';
        this.events = this.mergeEvents(this.events, [appended]);
        this.selectedThread = this.selectedThread ? {
          ...this.selectedThread,
          revision: this.selectedThread.revision + 1,
          events: this.mergeEvents(this.selectedThread.events, [appended]),
        } : null;
        this.render();
      },
      error: error => this.fail(error),
    });
  }

  search(): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected || this.searchQuery.trim().length < 2) return;
    this.api.search(hub, this.selected.workspace_id, this.searchQuery.trim()).subscribe({
      next: result => { this.searchResults = result.items; this.render(); },
      error: error => this.fail(error),
    });
  }

  toggleRoomLifecycle(): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected || !this.selectedRoom) return;
    this.busy = true;
    const state = this.selectedRoom.lifecycle_state === 'archived' ? 'active' : 'archived';
    this.api.transitionRoom(hub, this.selected.workspace_id, this.selectedRoom, state).subscribe({
      next: result => {
        this.busy = false;
        const updated: CollaborationRoom = {
          ...this.selectedRoom!,
          lifecycle_state: result.state,
          lifecycle_revision: result.revision,
          snapshot_digest: result.snapshot_digest,
        };
        this.selectedRoom = updated;
        this.rooms = this.rooms.map(room => room.room_id === updated.room_id ? updated : room);
        this.render();
      },
      error: error => this.fail(error),
    });
  }

  loadPresence(): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected || !this.selectedRoom) return;
    this.api.presence(hub, this.selected.workspace_id, this.selectedRoom.room_id).subscribe({
      next: result => { this.presence = result.items; this.render(); },
      error: error => this.fail(error),
    });
  }

  loadFlowProjection(): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected) return;
    this.api.flowProjection(hub, this.selected.workspace_id).subscribe({
      next: projection => { this.flow = projection; this.render(); },
      error: error => this.fail(error),
    });
  }

  projectionEntries(value: Record<string, Record<string, unknown>>): [string, Record<string, unknown>][] {
    return Object.entries(value);
  }

  hasProjectionEntries(): boolean {
    if (!this.flow) return false;
    const state = this.flow.state;
    return [state.tasks, state.workflows, state.git_refs, state.reviews, state.artifacts]
      .some(value => Object.keys(value).length > 0);
  }

  private async eventEnvelope(
    eventType: string,
    payload: Record<string, unknown>,
    threadId: string | null,
  ): Promise<Record<string, unknown>> {
    if (!this.selected || !this.selectedRoom) throw new Error('collaboration_room_required');
    return {
      schema: 'ananta.workspace-event.v1',
      event_id: `event-${crypto.randomUUID()}`,
      workspace_id: this.selected.workspace_id,
      room_id: this.selectedRoom.room_id,
      thread_id: threadId,
      event_type: eventType,
      actor_binding_id: this.selected.current_actor_binding_id || this.selected.created_by,
      idempotency_key: `${eventType}-${crypto.randomUUID()}`,
      correlation_id: `correlation-${crypto.randomUUID()}`,
      causation_id: threadId,
      visibility: this.selectedRoom.access_mode === 'restricted' ? 'restricted' : 'room',
      retention: 'standard',
      occurred_at: Date.now() / 1000,
      payload,
      payload_digest: await sha256(canonicalJson(payload)),
      source_refs: [],
      run_refs: [],
    };
  }

  private mergeEvents(current: CollaborationEvent[], incoming: CollaborationEvent[]): CollaborationEvent[] {
    const values = new Map(current.map(event => [event.event_id, event]));
    for (const event of incoming) values.set(event.event_id, event);
    return [...values.values()].sort((left, right) => left.sequence - right.sequence);
  }

  private hubUrl(): string {
    return this.agents.list().find(agent => agent.role === 'hub')?.url || '';
  }

  private fail(error: unknown): void {
    this.busy = false;
    const response = error as { status?: number; error?: { message?: string }; message?: string };
    if (response.status === 403) {
      this.selected = null;
      this.selectedRoom = null;
      this.selectedThread = null;
      this.rooms = [];
      this.events = [];
      this.memberships = [];
    }
    this.error = response.error?.message || response.message || 'Collaboration-Anfrage fehlgeschlagen.';
    this.render();
  }

  private render(): void { this.changeDetector.markForCheck(); }
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(item => canonicalJson(item)).join(',')}]`;
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(',')}}`;
  }
  const primitive = JSON.stringify(value);
  if (primitive === undefined) throw new Error('collaboration_payload_not_json');
  return primitive;
}
