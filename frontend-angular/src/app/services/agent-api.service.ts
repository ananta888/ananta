import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, timeout, retry } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';

@Injectable({ providedIn: 'root' })
export class AgentApiService {
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

  health(baseUrl: string, token?: string): Observable<any> {
    return this.http.get(`${baseUrl}/health`, this.getHeaders(baseUrl, token)).pipe(timeout(5000), retry(this.retryCount));
  }
  ready(baseUrl: string, token?: string): Observable<any> {
    return this.http.get(`${baseUrl}/ready`, this.getHeaders(baseUrl, token)).pipe(timeout(5000), retry(this.retryCount));
  }
  getConfig(baseUrl: string, token?: string): Observable<any> {
    return this.http.get(`${baseUrl}/config`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  setConfig(baseUrl: string, cfg: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/config`, cfg, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  propose(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/step/propose`, body, this.getHeaders(baseUrl, token)).pipe(timeout(60000)); // LLM calls take longer
  }
  execute(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/step/execute`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000));
  }
  logs(baseUrl: string, limit = 200, taskId?: string, token?: string): Observable<any> {
    const q = new URLSearchParams({ limit: String(limit), ...(taskId ? { task_id: taskId } : {}) });
    return this.http.get(`${baseUrl}/logs?${q.toString()}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  rotateToken(baseUrl: string, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/rotate-token`, {}, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs));
  }
  getMetrics(baseUrl: string, token?: string): Observable<string> {
    return this.http.get(`${baseUrl}/metrics`, { 
      headers: this.getHeaders(baseUrl, token).headers, 
      responseType: 'text' 
    }).pipe(timeout(this.timeoutMs));
  }
  llmGenerate(baseUrl: string, prompt: string, config?: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/llm/generate`, { prompt, config }, this.getHeaders(baseUrl, token)).pipe(timeout(120000));
  }
}
