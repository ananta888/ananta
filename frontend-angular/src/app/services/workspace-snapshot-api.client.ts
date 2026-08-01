import {
  HttpClient,
  HttpEvent,
  HttpHeaders,
  HttpParams,
} from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, throwError } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';

export interface WorkspaceSnapshotFile {
  readonly file: File;
  readonly relativePath: string;
}

export interface WorkspaceSnapshotUploadRequest {
  readonly projectId: string;
  readonly displayName: string;
  readonly files: readonly WorkspaceSnapshotFile[];
  readonly idempotencyKey: string;
}

export interface WorkspaceSnapshotUploadResponse {
  readonly workspace_id: string;
  readonly state: 'active';
  readonly file_count: number;
  readonly total_bytes: number;
  readonly replayed: boolean;
}

@Injectable({ providedIn: 'root' })
export class WorkspaceSnapshotApiClient {
  private readonly http = inject(HttpClient);
  private readonly directory = inject(AgentDirectoryService);

  upload(
    request: WorkspaceSnapshotUploadRequest,
  ): Observable<HttpEvent<WorkspaceSnapshotUploadResponse>> {
    const projectId = request.projectId.trim();
    const displayName = request.displayName.trim();
    const idempotencyKey = request.idempotencyKey.trim();
    const hub = this.directory.list().find((agent) => agent.role === 'hub');
    if (!hub || !projectId || !displayName || request.files.length === 0) {
      return throwError(() => new Error('workspace_snapshot_request_incomplete'));
    }

    const form = new FormData();
    form.append('display_name', displayName);
    for (const upload of request.files) {
      form.append('files', upload.file, upload.relativePath);
    }

    const url = `${hub.url.replace(/\/+$/, '')}/api/source-control/v1/workspace-snapshots`;
    return this.http.request<WorkspaceSnapshotUploadResponse>('POST', url, {
      body: form,
      headers: new HttpHeaders({ 'Idempotency-Key': idempotencyKey }),
      params: new HttpParams().set('project_id', projectId),
      observe: 'events',
      reportProgress: true,
    });
  }
}
