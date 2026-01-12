import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, timeout, retry } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class AgentApiService {
  private timeoutMs = 15000;
  private retryCount = 2;

  constructor(private http: HttpClient) {}

  health(baseUrl: string): Observable<any> {
    return this.http.get(`${baseUrl}/health`).pipe(timeout(5000), retry(this.retryCount));
  }
  ready(baseUrl: string): Observable<any> {
    return this.http.get(`${baseUrl}/ready`).pipe(timeout(5000), retry(this.retryCount));
  }
  getConfig(baseUrl: string): Observable<any> {
    return this.http.get(`${baseUrl}/config`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  setConfig(baseUrl: string, cfg: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/config`, cfg, { headers: this.auth(token) }).pipe(timeout(this.timeoutMs));
  }
  propose(baseUrl: string, body: any): Observable<any> {
    return this.http.post(`${baseUrl}/step/propose`, body).pipe(timeout(60000)); // LLM calls take longer
  }
  execute(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/step/execute`, body, { headers: this.auth(token) }).pipe(timeout(120000));
  }
  logs(baseUrl: string, limit = 200, taskId?: string): Observable<any> {
    const q = new URLSearchParams({ limit: String(limit), ...(taskId ? { task_id: taskId } : {}) });
    return this.http.get(`${baseUrl}/logs?${q.toString()}`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  rotateToken(baseUrl: string, token: string): Observable<any> {
    return this.http.post(`${baseUrl}/rotate-token`, {}, { headers: this.auth(token) }).pipe(timeout(this.timeoutMs));
  }
  getMetrics(baseUrl: string): Observable<string> {
    return this.http.get(`${baseUrl}/metrics`, { responseType: 'text' }).pipe(timeout(this.timeoutMs));
  }

  private auth(token?: string) {
    let headers = new HttpHeaders();
    if (token) headers = headers.set('Authorization', `Bearer ${token}`);
    return headers;
  }
}
