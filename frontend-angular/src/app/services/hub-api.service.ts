import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, timeout, retry, timer, map } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';

@Injectable({ providedIn: 'root' })
export class HubApiService {
  private http = inject(HttpClient);
  private dir = inject(AgentDirectoryService);
  private userAuth = inject(UserAuthService);

  private timeoutMs = 15000;
  private retryCount = 2;

  private getExponentialBackoff(initialDelay: number = 2000, maxDelay: number = 60000) {
    return {
      delay: (error: any, retryCount: number) => {
        const delay = Math.min(initialDelay * Math.pow(2, retryCount - 1), maxDelay);
        console.log(`SSE Reconnection Attempt ${retryCount}, delaying for ${delay}ms`);
        return timer(delay);
      }
    };
  }

  private getHeaders(baseUrl: string, token?: string) {
    let headers = new HttpHeaders();
    if (!token) {
      const hub = this.dir.list().find(a => a.role === 'hub');
      if (hub && baseUrl.startsWith(hub.url) && this.userAuth.token) {
        token = this.userAuth.token;
      } else {
        const agent = this.dir.list().find(a => baseUrl.startsWith(a.url));
        token = agent?.token;
      }
    }
    if (token) {
      headers = headers.set('Authorization', `Bearer ${token}`);
    }
    return { headers };
  }

  private unwrapResponse<T>(obs: Observable<T>): Observable<T> {
    return obs.pipe(
      map((response: any) => {
        // Unwrap one or more API envelope layers: { status, data, message? }.
        let current = response;
        for (let i = 0; i < 4; i += 1) {
          if (
            current &&
            typeof current === 'object' &&
            'status' in current &&
            'data' in current
          ) {
            current = current.data;
            continue;
          }
          break;
        }
        return current;
      })
    );
  }

  // Templates
  listTemplates(baseUrl: string, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/templates`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }
  createTemplate(baseUrl: string, tpl: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/templates`, tpl, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  updateTemplate(baseUrl: string, id: string, patch: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.patch(`${baseUrl}/templates/${id}`, patch, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  deleteTemplate(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.delete(`${baseUrl}/templates/${id}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }

  // Config
  listProviders(baseUrl: string, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/providers`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }

  // Tasks
  listTasks(baseUrl: string, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/tasks`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }
  getTask(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.get(`${baseUrl}/tasks/${id}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }
  createTask(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/tasks`, body, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  patchTask(baseUrl: string, id: string, patch: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.patch(`${baseUrl}/tasks/${id}`, patch, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  assign(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/tasks/${id}/assign`, body, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  propose(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/tasks/${id}/step/propose`, body, this.getHeaders(baseUrl, token)).pipe(timeout(60000)));
  }
  execute(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/tasks/${id}/step/execute`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
  }
  getAutopilotStatus(baseUrl: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.get(`${baseUrl}/tasks/autopilot/status`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }
  startAutopilot(
    baseUrl: string,
    body: {
      interval_seconds?: number;
      max_concurrency?: number;
      goal?: string;
      team_id?: string;
      budget_label?: string;
      security_level?: 'safe' | 'balanced' | 'aggressive';
    },
    token?: string
  ): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/tasks/autopilot/start`, body || {}, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  stopAutopilot(baseUrl: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/tasks/autopilot/stop`, {}, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  tickAutopilot(baseUrl: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/tasks/autopilot/tick`, {}, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  getTaskTimeline(
    baseUrl: string,
    filters?: { team_id?: string; agent?: string; status?: string; error_only?: boolean; limit?: number },
    token?: string
  ): Observable<any> {
    const q = new URLSearchParams();
    if (filters?.team_id) q.set('team_id', filters.team_id);
    if (filters?.agent) q.set('agent', filters.agent);
    if (filters?.status) q.set('status', filters.status);
    if (typeof filters?.error_only === 'boolean') q.set('error_only', filters.error_only ? '1' : '0');
    q.set('limit', String(filters?.limit || 200));
    const query = q.toString();
    return this.unwrapResponse(this.http.get(`${baseUrl}/tasks/timeline${query ? `?${query}` : ''}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }
  taskLogs(baseUrl: string, id: string, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/tasks/${id}/logs`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }

  // Archivierte Tasks
  listArchivedTasks(baseUrl: string, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/tasks/archived`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }
  archiveTask(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/tasks/${id}/archive`, {}, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  restoreTask(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/tasks/archived/${id}/restore`, {}, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }

  streamTaskLogs(baseUrl: string, id: string, token?: string): Observable<any> {
    return new Observable(observer => {
      let urlStr = `${baseUrl}/tasks/${id}/stream-logs`;
      
      if (!token) {
        const agent = this.dir.list().find(a => urlStr.startsWith(a.url));
        token = agent?.token;
      }
      if (token) {
        urlStr += (urlStr.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}`;
      }
      
      const eventSource = new EventSource(urlStr);
      
      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          observer.next(data);
        } catch (e) {
          console.error('SSE JSON parse error', e);
        }
      };
      
      eventSource.onerror = (error) => {
        if (eventSource.readyState === EventSource.CLOSED) {
          observer.complete();
        } else {
          observer.error(error);
        }
      };
      
      return () => {
        eventSource.close();
      };
    }).pipe(retry(this.getExponentialBackoff()));
  }

  streamSystemEvents(baseUrl: string, token?: string): Observable<any> {
    return new Observable(observer => {
      let urlStr = `${baseUrl}/api/system/events`;
      
      if (!token) {
        const hub = this.dir.list().find(a => a.role === 'hub');
        if (hub && urlStr.startsWith(hub.url) && this.userAuth.token) {
          token = this.userAuth.token;
        } else {
          const agent = this.dir.list().find(a => urlStr.startsWith(a.url));
          token = agent?.token;
        }
      }
      if (token) {
        urlStr += (urlStr.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}`;
      }
      
      const eventSource = new EventSource(urlStr);
      
      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          observer.next(data);
        } catch (e) {
          // Keep-alive ignoren
        }
      };
      
      eventSource.onerror = (error) => {
        observer.error(error);
      };
      
      return () => {
        eventSource.close();
      };
    }).pipe(retry(this.getExponentialBackoff()));
  }

  // Agents
  listAgents(baseUrl: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.get<any>(`${baseUrl}/api/system/agents`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }

  getStats(baseUrl: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.get<any>(`${baseUrl}/api/system/stats`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }

  getStatsHistory(baseUrl: string, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/api/system/stats/history`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }

  getAuditLogs(baseUrl: string, limit = 100, offset = 0, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/api/system/audit-logs?limit=${limit}&offset=${offset}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }

  analyzeAuditLogs(baseUrl: string, limit: number = 50, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/api/system/audit/analyze?limit=${limit}`, {}, this.getHeaders(baseUrl, token)).pipe(timeout(60000)));
  }

  // Teams
  listTeams(baseUrl: string, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/teams`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }
  listTeamTypes(baseUrl: string, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/teams/types`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  listTeamRoles(baseUrl: string, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/teams/roles`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  listRolesForTeamType(baseUrl: string, typeId: string, token?: string): Observable<any[]> {
    return this.unwrapResponse(this.http.get<any[]>(`${baseUrl}/teams/types/${typeId}/roles`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  createTeamType(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/teams/types`, body, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  createRole(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/teams/roles`, body, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  linkRoleToType(baseUrl: string, typeId: string, roleId: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/teams/types/${typeId}/roles`, { role_id: roleId }, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  updateRoleTemplateMapping(baseUrl: string, typeId: string, roleId: string, templateId: string | null, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.patch(`${baseUrl}/teams/types/${typeId}/roles/${roleId}`, { template_id: templateId }, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  unlinkRoleFromType(baseUrl: string, typeId: string, roleId: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.delete(`${baseUrl}/teams/types/${typeId}/roles/${roleId}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  deleteTeamType(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.delete(`${baseUrl}/teams/types/${id}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  deleteRole(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.delete(`${baseUrl}/teams/roles/${id}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  createTeam(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/teams`, body, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  patchTeam(baseUrl: string, id: string, patch: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.patch(`${baseUrl}/teams/${id}`, patch, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  deleteTeam(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.delete(`${baseUrl}/teams/${id}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  activateTeam(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/teams/${id}/activate`, {}, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }

  setupScrumTeam(baseUrl: string, name?: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/teams/setup-scrum`, { name }, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
}
