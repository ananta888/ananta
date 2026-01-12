import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, timeout, retry } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class HubApiService {
  private timeoutMs = 15000;
  private retryCount = 2;

  constructor(private http: HttpClient) {}

  private headers(token?: string) {
    let h = new HttpHeaders();
    if (token) h = h.set('Authorization', `Bearer ${token}`);
    return h;
  }

  // Templates
  listTemplates(baseUrl: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/templates`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  createTemplate(baseUrl: string, tpl: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/templates`, tpl, { headers: this.headers(token) }).pipe(timeout(this.timeoutMs));
  }
  updateTemplate(baseUrl: string, id: string, patch: any, token?: string): Observable<any> {
    return this.http.put(`${baseUrl}/templates/${id}`, patch, { headers: this.headers(token) }).pipe(timeout(this.timeoutMs));
  }
  deleteTemplate(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.http.delete(`${baseUrl}/templates/${id}`, { headers: this.headers(token) }).pipe(timeout(this.timeoutMs));
  }

  // Tasks
  listTasks(baseUrl: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/tasks`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  getTask(baseUrl: string, id: string): Observable<any> {
    return this.http.get(`${baseUrl}/tasks/${id}`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }
  createTask(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks`, body, { headers: this.headers(token) }).pipe(timeout(this.timeoutMs));
  }
  patchTask(baseUrl: string, id: string, patch: any, token?: string): Observable<any> {
    return this.http.patch(`${baseUrl}/tasks/${id}`, patch, { headers: this.headers(token) }).pipe(timeout(this.timeoutMs));
  }
  assign(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/assign`, body, { headers: this.headers(token) }).pipe(timeout(this.timeoutMs));
  }
  propose(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/step/propose`, body, { headers: this.headers(token) }).pipe(timeout(60000));
  }
  execute(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/step/execute`, body, { headers: this.headers(token) }).pipe(timeout(120000));
  }
  taskLogs(baseUrl: string, id: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/tasks/${id}/logs`).pipe(timeout(this.timeoutMs), retry(this.retryCount));
  }

  // Agents
  listAgents(baseUrl: string, token?: string): Observable<any> {
    return this.http.get<any>(`${baseUrl}/agents`, { headers: this.headers(token) }).pipe(timeout(this.timeoutMs));
  }
}
