import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from '../../../services/hub-api-core.service';

export interface CcProjectReadModel {
  id: string;
  name: string;
  description: string;
  is_active: boolean;
  root: string | null;
}

export interface CcTaskReadModel {
  id: string;
  title: string;
  description: string;
  status: string;
  priority: string;
  project_id?: string | null;
  verification_status?: Record<string, unknown>;
}

export interface CcSessionReadModel {
  id: string;
  task_id: string | null;
  title: string;
  status: string;
  transport: string;
  mode: string;
  owner_user_id: string;
}

export interface CcWorkerReadModel {
  id: string;
  runtime: string;
  health: string;
  capabilities: string[];
  boundary: string;
}

@Injectable({ providedIn: 'root' })
export class HubControlCenterApiClient {
  private core = inject(HubApiCoreService);

  listProjects(baseUrl: string, token?: string): Observable<{ items: CcProjectReadModel[]; count: number }> {
    return this.core.get<{ items: CcProjectReadModel[]; count: number }>(`${baseUrl}/api/projects`, baseUrl, token, false);
  }

  listProjectTasks(baseUrl: string, projectId: string, token?: string): Observable<{ items: CcTaskReadModel[]; count: number }> {
    return this.core.get<{ items: CcTaskReadModel[]; count: number }>(`${baseUrl}/api/projects/${encodeURIComponent(projectId)}/tasks`, baseUrl, token, false);
  }

  listSessions(baseUrl: string, taskId?: string, token?: string): Observable<{ items: CcSessionReadModel[]; count: number }> {
    const q = taskId ? `?task_id=${encodeURIComponent(taskId)}` : '';
    return this.core.get<{ items: CcSessionReadModel[]; count: number }>(`${baseUrl}/api/sessions${q}`, baseUrl, token, false);
  }

  listWorkers(baseUrl: string, token?: string): Observable<{ items: CcWorkerReadModel[]; count: number }> {
    return this.core.get<{ items: CcWorkerReadModel[]; count: number }>(`${baseUrl}/api/workers`, baseUrl, token, false);
  }
}
