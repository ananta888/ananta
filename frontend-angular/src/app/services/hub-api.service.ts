import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, timeout, retry } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';

@Injectable({ providedIn: 'root' })
export class HubApiService {
  private timeoutMs = 15000;
  private retryCount = 2;

  constructor(private http: HttpClient, private dir: AgentDirectoryService) {}

  private getHeaders(baseUrl: string, token?: string) {
    let headers = new HttpHeaders();
    if (!token) {
      const agent = this.dir.list().find(a => baseUrl.startsWith(a.url));
      token = agent?.token;
    }
    if (token) {
      headers = headers.set('Authorization', `Bearer ${token}`);
    }
    return { headers };
  }

  // Templates
  listTemplates(baseUrl: string, token?: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/templates`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  createTemplate(baseUrl: string, tpl: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/templates`, tpl, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  updateTemplate(baseUrl: string, id: string, patch: any, token?: string): Observable<any> {
    return this.http.put(`${baseUrl}/templates/${id}`, patch, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  deleteTemplate(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.http.delete(`${baseUrl}/templates/${id}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }

  // Tasks
  listTasks(baseUrl: string, token?: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/tasks`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  getTask(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.http.get(`${baseUrl}/tasks/${id}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  createTask(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks`, body, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  patchTask(baseUrl: string, id: string, patch: any, token?: string): Observable<any> {
    return this.http.patch(`${baseUrl}/tasks/${id}`, patch, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  assign(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/assign`, body, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  propose(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/step/propose`, body, this.getHeaders(baseUrl, token)).pipe(timeout(60000));
  }
  execute(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/step/execute`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000));
  }
  taskLogs(baseUrl: string, id: string, token?: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/tasks/${id}/logs`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount));
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
    });
  }

  streamSystemEvents(baseUrl: string, token?: string): Observable<any> {
    return new Observable(observer => {
      let urlStr = `${baseUrl}/events`;
      
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
          // Keep-alive ignoren
        }
      };
      
      eventSource.onerror = (error) => {
        observer.error(error);
      };
      
      return () => {
        eventSource.close();
      };
    }).pipe(retry({ delay: 5000 }));
  }

  // Agents
  listAgents(baseUrl: string, token?: string): Observable<any> {
    return this.http.get<any>(`${baseUrl}/agents`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }

  getStats(baseUrl: string, token?: string): Observable<any> {
    return this.http.get<any>(`${baseUrl}/stats`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }

  getStatsHistory(baseUrl: string, token?: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/stats/history`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }

  getAuditLogs(baseUrl: string, limit = 100, offset = 0, token?: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/audit-logs?limit=${limit}&offset=${offset}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }

  // Teams
  listTeams(baseUrl: string, token?: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/teams`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  listTeamTypes(baseUrl: string, token?: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/teams/types`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  listTeamRoles(baseUrl: string, token?: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/teams/roles`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  listRolesForTeamType(baseUrl: string, typeId: string, token?: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/teams/types/${typeId}/roles`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  createTeamType(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/teams/types`, body, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  createRole(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/teams/roles`, body, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  linkRoleToType(baseUrl: string, typeId: string, roleId: string, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/teams/types/${typeId}/roles`, { role_id: roleId }, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  updateRoleTemplateMapping(baseUrl: string, typeId: string, roleId: string, templateId: string | null, token?: string): Observable<any> {
    return this.http.patch(`${baseUrl}/teams/types/${typeId}/roles/${roleId}`, { template_id: templateId }, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  unlinkRoleFromType(baseUrl: string, typeId: string, roleId: string, token?: string): Observable<any> {
    return this.http.delete(`${baseUrl}/teams/types/${typeId}/roles/${roleId}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  deleteTeamType(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.http.delete(`${baseUrl}/teams/types/${id}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  deleteRole(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.http.delete(`${baseUrl}/teams/roles/${id}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  createTeam(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/teams`, body, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  patchTeam(baseUrl: string, id: string, patch: any, token?: string): Observable<any> {
    return this.http.patch(`${baseUrl}/teams/${id}`, patch, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  deleteTeam(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.http.delete(`${baseUrl}/teams/${id}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  activateTeam(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/teams/${id}/activate`, {}, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
}
