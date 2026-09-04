import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import {
  CollaborationEvent,
  CollaborationPage,
  CollaborationRoom,
  CollaborationMembership,
  CollaborationFlowProjection,
  CollaborationPresence,
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

  private endpoint(hubUrl: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/collaboration/workspaces`;
  }
}
