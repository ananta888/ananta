import { ChangeDetectorRef, Component, Input, inject } from '@angular/core';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { CollaborationWorkspaceApiService } from './collaboration-workspace-api.service';
import { CollaborationLiveStateService, RemoteControlGrant } from './collaboration-live-state.service';
import {
  CollaborationMembership,
  CollaborationPresence,
  CollaborationRoom,
  CollaborationWorkspaceSummary,
} from './collaboration-workspace.models';

@Component({
  selector: 'app-collaboration-live-panel',
  standalone: true,
  imports: [],
  template: `
    @for (membership of memberships; track membership.actor_binding_id) {
      <article>
        <strong>{{ membership.actor.display_name }} · {{ membership.actor.actor_kind }}</strong>
        @if (membership.actor.profile) {
          <small>Profil: {{ membership.actor.profile.provider }} / {{ membership.actor.profile.model }} · {{ membership.actor.profile.profile_revision }}</small>
        }
        <span>{{ membership.role }} · {{ membership.status }} · Revision {{ membership.revision }}</span>
        <small>Rechte: {{ membership.effective_capabilities?.join(', ') || 'rollenbasiert / serverseitig' }}</small>
        <small>{{ capabilityReason(membership, 'event.write') }}</small>
        <small>Presence: {{ presenceFor(membership.actor_binding_id)?.presence_state || 'unbekannt' }} (keine Autorität)</small>
        <div class="actions">
          <button type="button" (click)="toggleFollow(membership.actor_binding_id)">
            {{ live.followedActor === membership.actor_binding_id ? 'Follow beenden' : 'Actor folgen' }}
          </button>
          @if (canGrantTo(membership)) {
            <button type="button" (click)="grantControl(membership.actor_binding_id)">Steuerung gewähren</button>
          }
        </div>
      </article>
    }
    @if (workspace && room) {
      <div class="actions">
        <button type="button" (click)="publishCursor()">Eigenen Cursor senden</button>
        <button type="button" (click)="loadLiveState()">Live-Zustand aktualisieren</button>
        @if (controlGrant) {
          <button type="button" (click)="revokeControl()">Steuerung lokal beenden</button>
        }
      </div>
      @for (cursor of cursors(); track cursor.cursor_id) {
        <small class="cursor">{{ cursor.actor_binding_id }} · {{ cursor.view_id }} · {{ cursor.x }}, {{ cursor.y }}</small>
      }
      @if (controlGrant) {
        <small>Control: {{ controlGrant.controller_actor_binding_id }} → {{ controlGrant.controlled_actor_binding_id }} · Revision {{ controlGrant.revision }}</small>
      }
    }
    @if (error) { <p class="error" role="alert">{{ error }}</p> }
  `,
  styles: [`
    :host, article { display:grid; gap:6px; }
    article { padding:8px; margin-block:6px; border:1px solid var(--border-color); border-radius:6px; }
    .actions { display:flex; gap:8px; flex-wrap:wrap; margin-block:8px; }
    .cursor { display:block; padding:4px; }
    .error { color:var(--danger); }
  `],
})
export class CollaborationLivePanelComponent {
  private readonly api = inject(CollaborationWorkspaceApiService);
  private readonly agents = inject(AgentDirectoryService);
  private readonly changeDetector = inject(ChangeDetectorRef);
  readonly live = inject(CollaborationLiveStateService);

  @Input() workspace: CollaborationWorkspaceSummary | null = null;
  @Input() room: CollaborationRoom | null = null;
  @Input() memberships: CollaborationMembership[] = [];
  @Input() presence: CollaborationPresence[] = [];

  controlGrant: RemoteControlGrant | null = null;
  error = '';
  private readonly viewId = 'workspace-main';
  private readonly epoch = 1;

  cursors() {
    return this.room ? this.live.visibleCursors(this.room.room_id, this.viewId) : [];
  }

  presenceFor(actorBindingId: string): CollaborationPresence | undefined {
    return this.presence.find(item => item.actor_binding_id === actorBindingId);
  }

  capabilityReason(membership: CollaborationMembership, capability: string): string {
    if (membership.status !== 'active') return `${capability}: denied_membership_revoked`;
    return membership.effective_capabilities?.includes(capability)
      ? `${capability}: allowed_by_role_${membership.role}`
      : `${capability}: denied_by_role_${membership.role}`;
  }

  toggleFollow(actorBindingId: string): void {
    if (this.live.followedActor === actorBindingId) this.live.stopFollow();
    else this.live.startFollow(actorBindingId);
    this.changeDetector.markForCheck();
  }

  canGrantTo(membership: CollaborationMembership): boolean {
    return Boolean(
      this.workspace && membership.status === 'active' &&
      membership.actor_binding_id !== this.workspace.current_actor_binding_id,
    );
  }

  publishCursor(): void {
    const context = this.context();
    if (!context) return;
    this.api.publishCursor(context.hub, context.workspaceId, context.roomId, {
      view_id: this.viewId, x: 0.5, y: 0.5, epoch: this.epoch, ttl_seconds: 10,
    }).subscribe({
      next: cursor => {
        this.live.upsertCursor(cursor, { room_id: context.roomId, view_id: this.viewId, epoch: this.epoch });
        this.changeDetector.markForCheck();
      },
      error: error => this.fail(error),
    });
  }

  loadLiveState(): void {
    const context = this.context();
    if (!context) return;
    this.api.liveCursors(context.hub, context.workspaceId, context.roomId, this.viewId).subscribe({
      next: result => {
        for (const cursor of result.items) {
          this.live.upsertCursor(cursor, { room_id: context.roomId, view_id: this.viewId, epoch: this.epoch });
        }
        this.loadControl(context.hub, context.workspaceId);
      },
      error: error => this.fail(error),
    });
  }

  grantControl(controllerActorBindingId: string): void {
    const context = this.context();
    if (!context) return;
    const controlledActor = this.workspace?.current_actor_binding_id;
    const expectedRevision = this.controlGrant?.controlled_actor_binding_id === controlledActor
      ? this.controlGrant.revision
      : 0;
    this.api.grantControl(context.hub, context.workspaceId, context.roomId, {
      controller_actor_binding_id: controllerActorBindingId,
      session_id: 'workspace-live-session',
      view_id: this.viewId,
      epoch: this.epoch,
      expected_revision: expectedRevision,
      ttl_seconds: 120,
    }).subscribe({
      next: grant => this.applyGrant(grant, context.roomId),
      error: error => this.fail(error),
    });
  }

  revokeControl(): void {
    const context = this.context();
    if (!context || !this.controlGrant) return;
    this.api.revokeControl(context.hub, context.workspaceId, this.controlGrant.revision).subscribe({
      next: () => {
        this.live.revokeControl('local_stop');
        this.controlGrant = null;
        this.changeDetector.markForCheck();
      },
      error: error => this.fail(error),
    });
  }

  private loadControl(hub: string, workspaceId: string): void {
    this.api.currentControl(hub, workspaceId).subscribe({
      next: grant => {
        if (grant && this.room) this.applyGrant(grant, this.room.room_id);
        else this.controlGrant = null;
        this.changeDetector.markForCheck();
      },
      error: error => this.fail(error),
    });
  }

  private applyGrant(grant: RemoteControlGrant, roomId: string): void {
    const accepted = this.live.applyControlGrant(grant, {
      session_id: 'workspace-live-session', room_id: roomId, view_id: this.viewId, epoch: this.epoch,
    });
    this.controlGrant = accepted ? grant : null;
    this.changeDetector.markForCheck();
  }

  private context(): { hub: string; workspaceId: string; roomId: string } | null {
    const hub = this.agents.list().find(agent => agent.role === 'hub')?.url || '';
    if (!hub || !this.workspace || !this.room) return null;
    return { hub, workspaceId: this.workspace.workspace_id, roomId: this.room.room_id };
  }

  private fail(error: unknown): void {
    const response = error as { error?: { message?: string }; message?: string };
    this.error = response.error?.message || response.message || 'Live-Anfrage fehlgeschlagen.';
    this.changeDetector.markForCheck();
  }
}
