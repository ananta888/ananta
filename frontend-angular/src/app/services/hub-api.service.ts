import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class HubApiService {
  constructor(private http: HttpClient) {}

  private headers(token?: string) {
    let h = new HttpHeaders();
    if (token) h = h.set('Authorization', `Bearer ${token}`);
    return h;
  }

  // Templates
  listTemplates(baseUrl: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/templates`);
  }
  createTemplate(baseUrl: string, tpl: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/templates`, tpl, { headers: this.headers(token) });
  }
  updateTemplate(baseUrl: string, id: string, patch: any, token?: string): Observable<any> {
    return this.http.put(`${baseUrl}/templates/${id}`, patch, { headers: this.headers(token) });
  }
  deleteTemplate(baseUrl: string, id: string, token?: string): Observable<any> {
    return this.http.delete(`${baseUrl}/templates/${id}`, { headers: this.headers(token) });
  }

  // Tasks
  listTasks(baseUrl: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/tasks`);
  }
  getTask(baseUrl: string, id: string): Observable<any> {
    return this.http.get(`${baseUrl}/tasks/${id}`);
  }
  createTask(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks`, body, { headers: this.headers(token) });
  }
  patchTask(baseUrl: string, id: string, patch: any, token?: string): Observable<any> {
    return this.http.patch(`${baseUrl}/tasks/${id}`, patch, { headers: this.headers(token) });
  }
  assign(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/assign`, body, { headers: this.headers(token) });
  }
  propose(baseUrl: string, id: string, body: any): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/propose`, body);
  }
  execute(baseUrl: string, id: string, body: any, token?: string): Observable<any> {
    return this.http.post(`${baseUrl}/tasks/${id}/execute`, body, { headers: this.headers(token) });
  }
  taskLogs(baseUrl: string, id: string): Observable<any[]> {
    return this.http.get<any[]>(`${baseUrl}/tasks/${id}/logs`);
  }
}
