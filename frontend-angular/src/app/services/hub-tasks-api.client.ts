import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubTasksApiClient {
  private core = inject(HubApiCoreService);

  listTasks(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/tasks`, baseUrl, token, true); }
  getTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.get(`${baseUrl}/tasks/${id}`, baseUrl, token, true); }
  createTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks`, body, baseUrl, token); }
  patchTask(baseUrl: string, id: string, patch: any, token?: string): Observable<any> { return this.core.patch(`${baseUrl}/tasks/${id}`, patch, baseUrl, token); }
  assign(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/${id}/assign`, body, baseUrl, token); }
  propose(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/${id}/step/propose`, body, baseUrl, token, false, 60000); }
  execute(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/${id}/step/execute`, body, baseUrl, token, false, 120000); }

  getTaskTimeline(baseUrl: string, filters?: { team_id?: string; agent?: string; status?: string; error_only?: boolean; limit?: number }, token?: string): Observable<any> {
    const q = new URLSearchParams();
    if (filters?.team_id) q.set('team_id', filters.team_id);
    if (filters?.agent) q.set('agent', filters.agent);
    if (filters?.status) q.set('status', filters.status);
    if (typeof filters?.error_only === 'boolean') q.set('error_only', filters.error_only ? '1' : '0');
    q.set('limit', String(filters?.limit || 200));
    const query = q.toString();
    return this.core.get(`${baseUrl}/tasks/timeline${query ? `?${query}` : ''}`, baseUrl, token, true);
  }

  getTaskOrchestrationReadModel(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/tasks/orchestration/read-model`, baseUrl, token, true); }
  ingestOrchestrationTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post<any>(`${baseUrl}/tasks/orchestration/ingest`, body, baseUrl, token); }
  claimOrchestrationTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post<any>(`${baseUrl}/tasks/orchestration/claim`, body, baseUrl, token); }
  completeOrchestrationTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post<any>(`${baseUrl}/tasks/orchestration/complete`, body, baseUrl, token); }

  taskLogs(baseUrl: string, id: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/tasks/${id}/logs`, baseUrl, token, true); }
  streamTaskLogs(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.streamTaskLogs(baseUrl, id, token); }

  listArchivedTasks(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/tasks/archived`, baseUrl, token, true); }
  archiveTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/${id}/archive`, {}, baseUrl, token); }
  restoreTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/archived/${id}/restore`, {}, baseUrl, token); }
}
