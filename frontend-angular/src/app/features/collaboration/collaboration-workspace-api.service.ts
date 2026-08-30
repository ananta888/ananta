import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import {
  CollaborationEvent,
  CollaborationPage,
  CollaborationRoom,
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
  ): Observable<CollaborationWorkspaceSummary & { rooms: CollaborationRoom[] }> {
    return this.core.get<CollaborationWorkspaceSummary & { rooms: CollaborationRoom[] }>(
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

  private endpoint(hubUrl: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/collaboration/workspaces`;
  }
}
