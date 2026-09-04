import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import { CollaborationCursor, RemoteControlGrant } from './collaboration-live-state.service';
import {
  CollaborationEvent,
  CollaborationPage,
  CollaborationRoom,
  CollaborationMembership,
  CollaborationFlowProjection,
  CollaborationPresence,
  CollaborationResourceOffer,
  CollaborationThread,
  CollaborationWorkspaceSummary,
} from './collaboration-workspace.models';

@Injectable({ providedIn: 'root' })
export class CollaborationWorkspaceApiService {
  private readonly core = inject(HubApiCoreService);

  list(hubUrl: string): Observable<CollaborationPage<CollaborationWorkspaceSummary>> {
    return this.core.get<CollaborationPage<CollaborationWorkspaceSummary>>(this.endpoint(hubUrl), hubUrl);
  }

  create(hubUrl: string, title: string): Observable<CollaborationWorkspaceSummary> {
    return this.core.post<CollaborationWorkspaceSummary>(
      this.endpoint(hubUrl), { title, display_name: 'Workspace Owner' }, hubUrl,
    );
  }

  get(
    hubUrl: string,
    workspaceId: string,
  ): Observable<CollaborationWorkspaceSummary & { rooms: CollaborationRoom[]; memberships: CollaborationMembership[] }> {
    return this.core.get<CollaborationWorkspaceSummary & { rooms: CollaborationRoom[]; memberships: CollaborationMembership[] }>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}`, hubUrl,
    );
  }

  createRoom(hubUrl: string, workspaceId: string, room: CollaborationRoom): Observable<CollaborationRoom> {
    return this.core.post<CollaborationRoom>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/rooms`, room, hubUrl,
    );
  }

  timeline(
    hubUrl: string,
    workspaceId: string,
    roomId: string,
  ): Observable<CollaborationPage<CollaborationEvent>> {
    const query = new URLSearchParams({ room_id: roomId }).toString();
    return this.core.get<CollaborationPage<CollaborationEvent>>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/timeline?${query}`, hubUrl,
    );
  }

  append(
    hubUrl: string,
    workspaceId: string,
    event: Record<string, unknown>,
  ): Observable<CollaborationEvent> {
    return this.core.post<CollaborationEvent>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/events`, event, hubUrl,
    );
  }

  thread(hubUrl: string, workspaceId: string, threadId: string): Observable<CollaborationThread> {
    return this.core.get<CollaborationThread>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/threads/${encodeURIComponent(threadId)}`,
      hubUrl,
    );
  }

  search(
    hubUrl: string,
    workspaceId: string,
    query: string,
  ): Observable<CollaborationPage<CollaborationEvent>> {
    const params = new URLSearchParams({ q: query }).toString();
    return this.core.get<CollaborationPage<CollaborationEvent>>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/search?${params}`,
      hubUrl,
    );
  }

  transitionRoom(
    hubUrl: string,
    workspaceId: string,
    room: CollaborationRoom,
    state: 'active' | 'archived',
  ): Observable<{ state: 'active' | 'archived'; revision: number; snapshot_digest: string }> {
    return this.core.put<{ state: 'active' | 'archived'; revision: number; snapshot_digest: string }>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/rooms/${encodeURIComponent(room.room_id)}/lifecycle`,
      { state, expected_revision: room.lifecycle_revision || 1 },
      hubUrl,
    );
  }

  presence(
    hubUrl: string,
    workspaceId: string,
    roomId: string,
  ): Observable<{ items: CollaborationPresence[]; room_id: string }> {
    return this.core.get<{ items: CollaborationPresence[]; room_id: string }>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/rooms/${encodeURIComponent(roomId)}/presence`,
      hubUrl,
    );
  }

  flowProjection(hubUrl: string, workspaceId: string): Observable<CollaborationFlowProjection> {
    return this.core.get<CollaborationFlowProjection>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/flow-projection`, hubUrl,
    );
  }

  resourceOffers(hubUrl: string, workspaceId: string): Observable<CollaborationPage<CollaborationResourceOffer>> {
    return this.core.get<CollaborationPage<CollaborationResourceOffer>>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/resource-offers`,
      hubUrl,
      undefined,
      false,
    );
  }

  liveCursors(
    hubUrl: string,
    workspaceId: string,
    roomId: string,
    viewId: string,
  ): Observable<{ items: CollaborationCursor[]; server_time: number }> {
    const query = new URLSearchParams({ view_id: viewId }).toString();
    return this.core.get<{ items: CollaborationCursor[]; server_time: number }>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/rooms/${encodeURIComponent(roomId)}/live/cursors?${query}`,
      hubUrl,
      undefined,
      false,
    );
  }

  publishCursor(
    hubUrl: string,
    workspaceId: string,
    roomId: string,
    cursor: { view_id: string; x: number; y: number; epoch: number; ttl_seconds: number },
  ): Observable<CollaborationCursor> {
    return this.core.put<CollaborationCursor>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/rooms/${encodeURIComponent(roomId)}/live/cursor`,
      cursor,
      hubUrl,
    );
  }

  grantControl(
    hubUrl: string,
    workspaceId: string,
    roomId: string,
    request: {
      controller_actor_binding_id: string;
      session_id: string;
      view_id: string;
      epoch: number;
      expected_revision: number;
      ttl_seconds: number;
    },
  ): Observable<RemoteControlGrant> {
    return this.core.put<RemoteControlGrant>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/rooms/${encodeURIComponent(roomId)}/live/control`,
      request,
      hubUrl,
    );
  }

  currentControl(hubUrl: string, workspaceId: string): Observable<RemoteControlGrant | null> {
    return this.core.get<RemoteControlGrant | null>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/live/control`,
      hubUrl,
      undefined,
      false,
    );
  }

  revokeControl(
    hubUrl: string,
    workspaceId: string,
    expectedRevision: number,
  ): Observable<{ revoked: boolean; revision?: number; reason_code: string }> {
    const query = new URLSearchParams({ expected_revision: String(expectedRevision) }).toString();
    return this.core.delete<{ revoked: boolean; revision?: number; reason_code: string }>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(workspaceId)}/live/control?${query}`,
      hubUrl,
    );
  }

  private endpoint(hubUrl: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/collaboration/workspaces`;
  }
}
