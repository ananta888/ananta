import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, timeout, retry } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';

@Injectable({ providedIn: 'root' })
export class HubApiService {
  private timeoutMs = 15000;
  private retryCount = 2;

  constructor(private http: HttpClient, private dir: AgentDirectoryService) {}

  // Templates
  listTemplates(baseUrl: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/templates`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  createTemplate(baseUrl: string, tpl: any): Observable<any> {
    return this.http.post(`${baseUrl}/templates`, tpl).pipe(timeout(this.timeoutMs));
  }
  updateTemplate(baseUrl: string, id: string, patch: any): Observable<any> {
    return this.http.put(`${baseUrl}/templates/${id}`, patch).pipe(timeout(this.timeoutMs));
  }
  deleteTemplate(baseUrl: string, id: string): Observable<any> {
    return this.http.delete(`${baseUrl}/templates/${id}`).pipe(timeout(this.timeoutMs));
  }

  // Tasks
  listTasks(baseUrl: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/tasks`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  getTask(baseUrl: string, id: string): Observable<any> {
    return this.http.get(`${baseUrl}/tasks/${id}`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  createTask(baseUrl: string, body: any): Observable<any> {
    return this.http.post(`${baseUrl}/tasks`, body).pipe(timeout(this.timeoutMs));
  }
  patchTask(baseUrl: string, id: string, patch: any): Observable<any> {
    return this.http.patch(`${baseUrl}/tasks/${id}`, patch).pipe(timeout(this.timeoutMs));
  }
  assign(baseUrl: string, id: string, body: any): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/assign`, body).pipe(timeout(this.timeoutMs));
  }
  propose(baseUrl: string, id: string, body: any): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/step/propose`, body).pipe(timeout(60000));
  }
  execute(baseUrl: string, id: string, body: any): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/step/execute`, body).pipe(timeout(120000));
  }
  taskLogs(baseUrl: string, id: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/tasks/${id}/logs`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }

  streamTaskLogs(baseUrl: string, id: string): Observable<any> {
    return new Observable(observer => {
      let urlStr = `${baseUrl}/tasks/${id}/stream-logs`;
      
      const agent = this.dir.list().find(a => urlStr.startsWith(a.url));
      if (agent && agent.token) {
        urlStr += (urlStr.includes('?') ? '&' : '?') + `token=${encodeURIComponent(agent.token)}`;
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

  streamSystemEvents(baseUrl: string): Observable<any> {
    return new Observable(observer => {
      let urlStr = `${baseUrl}/events`;
      
      const agent = this.dir.list().find(a => urlStr.startsWith(a.url));
      if (agent && agent.token) {
        urlStr += (urlStr.includes('?') ? '&' : '?') + `token=${encodeURIComponent(agent.token)}`;
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
  listAgents(baseUrl: string): Observable<any> {
    return this.http.get<any>(`${baseUrl}/agents`).pipe(timeout(this.timeoutMs));
  }

  getStats(baseUrl: string): Observable<any> {
    return this.http.get<any>(`${baseUrl}/stats`).pipe(timeout(this.timeoutMs));
  }

  getStatsHistory(baseUrl: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/stats/history`).pipe(timeout(this.timeoutMs));
  }

  // Teams
  listTeams(baseUrl: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/teams`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  createTeam(baseUrl: string, body: any): Observable<any> {
    return this.http.post(`${baseUrl}/teams`, body).pipe(timeout(this.timeoutMs));
  }
  patchTeam(baseUrl: string, id: string, patch: any): Observable<any> {
    return this.http.patch(`${baseUrl}/teams/${id}`, patch).pipe(timeout(this.timeoutMs));
  }
  deleteTeam(baseUrl: string, id: string): Observable<any> {
    return this.http.delete(`${baseUrl}/teams/${id}`).pipe(timeout(this.timeoutMs));
  }
  activateTeam(baseUrl: string, id: string): Observable<any> {
    return this.http.post(`${baseUrl}/teams/${id}/activate`, {}).pipe(timeout(this.timeoutMs));
  }
}
