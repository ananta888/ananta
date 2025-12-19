import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class AgentApiService {
  constructor(private http: HttpClient) {}

  health(baseUrl: string): Observable<any> {
    return this.http.get(`${baseUrl}/health`);
  }
  getConfig(baseUrl: string): Observable<any> {
    return this.http.get(`${baseUrl}/config`);
  }
  setConfig(baseUrl: string, cfg: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/config`, cfg, { headers: this.auth(token) });
  }
  propose(baseUrl: string, body: any): Observable<any> {
    return this.http.post(`${baseUrl}/step/propose`, body);
  }
  execute(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/step/execute`, body, { headers: this.auth(token) });
  }
  logs(baseUrl: string, limit = 200, taskId?: string): Observable<any> {
    const q = new URLSearchParams({ limit: String(limit), ...(taskId ? { task_id: taskId } : {}) });
    return this.http.get(`${baseUrl}/logs?${q.toString()}`);
  }

  private auth(token?: string) {
    let headers = new HttpHeaders();
    if (token) headers = headers.set('Authorization', `Bearer ${token}`);
    return headers;
  }
}
