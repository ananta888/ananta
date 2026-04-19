import { Injectable, inject } from '@angular/core';
import { Observable, retry, timeout } from 'rxjs';

import { AgentApiTransport } from './agent-api-transport.service';

/** Workspace-Sicht auf Tasks (Dateilisten, Artefakte). */
@Injectable({ providedIn: 'root' })
export class WorkspaceApiClient {
  private transport = inject(AgentApiTransport);

  taskWorkspaceFiles(
    baseUrl: string,
    taskId: string,
    token?: string,
    options?: { trackedOnly?: boolean; maxEntries?: number },
  ): Observable<any> {
    const trackedOnly = options?.trackedOnly ?? true;
    const maxEntries = Number(options?.maxEntries || 2000);
    const q = new URLSearchParams({
      tracked_only: trackedOnly ? '1' : '0',
      max_entries: String(
        Number.isFinite(maxEntries) ? Math.max(1, Math.min(maxEntries, 10000)) : 2000,
      ),
    });
    return this.transport.unwrap(
      this.transport.http
        .get(
          `${baseUrl}/tasks/${encodeURIComponent(taskId)}/workspace/files?${q.toString()}`,
          this.transport.getHeaders(baseUrl, token),
        )
        .pipe(timeout(45000), retry(1)),
    );
  }
}
