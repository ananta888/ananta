import { JsonPipe } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PageIntroComponent, SectionCardComponent } from '../../shared/ui/layout';
import { CollaborationWorkspaceApiService } from './collaboration-workspace-api.service';
import {
  CollaborationEvent,
  CollaborationRoom,
  CollaborationWorkspaceSummary,
} from './collaboration-workspace.models';

@Component({
  selector: 'app-collaboration-workspace-page',
  standalone: true,
  imports: [FormsModule, JsonPipe, PageIntroComponent, SectionCardComponent],
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
              <button type="button" class="entry" (click)="openRoom(room)">{{ room.title }}</button>
            }
          </div>
          @if (selectedRoom) {
            <div class="timeline">
              @for (event of events; track event.event_id) {
                <article>
                  <strong>{{ event.event_type }}</strong>
                  <span>{{ event.payload['text'] || (event.payload | json) }}</span>
                </article>
              }
            </div>
            <div class="row">
              <input [(ngModel)]="message" maxlength="4000" placeholder="Nachricht" />
              <button type="button" (click)="send()" [disabled]="busy || !message.trim()">Senden</button>
            </div>
          }
        } @else {
          <p>Workspace auswählen oder neu erstellen.</p>
        }
        @if (error) { <p class="error" role="alert">{{ error }}</p> }
      </app-section-card>
    </div>
  `,
  styles: [`
    .grid { display:grid; grid-template-columns:minmax(240px,1fr) minmax(320px,2fr); gap:16px; }
    .row,.rooms { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
    input { min-width:180px; flex:1; }
    .entry { display:block; width:100%; margin:6px 0; text-align:left; }
    .timeline { display:grid; gap:8px; max-height:420px; overflow:auto; margin:12px 0; }
    article { display:grid; gap:4px; padding:8px; border:1px solid var(--border-color); border-radius:6px; }
    .error { color:var(--danger); }
    @media(max-width:760px){.grid{grid-template-columns:1fr;}}
  `],
})
export class CollaborationWorkspacePageComponent implements OnInit {
  private readonly api = inject(CollaborationWorkspaceApiService);
  private readonly agents = inject(AgentDirectoryService);
  workspaces: CollaborationWorkspaceSummary[] = [];
  rooms: CollaborationRoom[] = [];
  events: CollaborationEvent[] = [];
  selected: CollaborationWorkspaceSummary | null = null;
  selectedRoom: CollaborationRoom | null = null;
  newTitle = '';
  newRoomTitle = '';
  message = '';
  busy = false;
  error = '';

  ngOnInit(): void { this.load(); }

  load(): void {
    const hub = this.hubUrl();
    if (!hub) { this.error = 'Kein Hub konfiguriert.'; return; }
    this.api.list(hub).subscribe({
      next: page => { this.workspaces = page.items; },
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
      },
      error: error => this.fail(error),
    });
  }

  open(workspace: CollaborationWorkspaceSummary): void {
    const hub = this.hubUrl();
    if (!hub) return;
    this.selected = workspace;
    this.selectedRoom = null;
    this.events = [];
    this.api.get(hub, workspace.workspace_id).subscribe({
      next: detail => { this.rooms = detail.rooms; },
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
      },
      error: error => this.fail(error),
    });
  }

  openRoom(room: CollaborationRoom): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected) return;
    this.selectedRoom = room;
    this.api.timeline(hub, this.selected.workspace_id, room.room_id).subscribe({
      next: page => { this.events = page.items; },
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
      },
      error: error => this.fail(error),
    });
  }

  private hubUrl(): string {
    return this.agents.list().find(agent => agent.role === 'hub')?.url || '';
  }

  private fail(error: unknown): void {
    this.busy = false;
    const response = error as { error?: { message?: string }; message?: string };
    this.error = response.error?.message || response.message || 'Collaboration-Anfrage fehlgeschlagen.';
  }
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
