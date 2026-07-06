import { Injectable, inject } from '@angular/core';
import { Observable, retry, timeout } from 'rxjs';

import { AgentApiTransport } from './agent-api-transport.service';

/** System-nahe Endpunkte: Health, Ready, Config, Metrics, Logs, Token-Rotation. */
@Injectable({ providedIn: 'root' })
export class SystemApiClient {
  private transport = inject(AgentApiTransport);
  readonly configTimeoutMs = 45000;
  readonly readinessTimeoutMs = 12000;

  health(baseUrl: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .get(`${baseUrl}/health`, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(5000), retry(this.transport.retryCount)),
    );
  }

  ready(baseUrl: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .get(`${baseUrl}/ready`, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(this.readinessTimeoutMs), retry(this.transport.retryCount)),
    );
  }

  getConfig(baseUrl: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .get(`${baseUrl}/config`, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(this.transport.timeoutMs), retry(this.transport.retryCount)),
    );
  }

  getEvolutionProviders(baseUrl: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .get(`${baseUrl}/evolution/providers`, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(this.transport.timeoutMs), retry(this.transport.retryCount)),
    );
  }

  setConfig(baseUrl: string, cfg: unknown, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/config`, cfg, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(this.configTimeoutMs)),
    );
  }

  logs(baseUrl: string, limit = 200, taskId?: string, token?: string): Observable<any> {
    const q = new URLSearchParams({ limit: String(limit), ...(taskId ? { task_id: taskId } : {}) });
    return this.transport.unwrap(
      this.transport.http
        .get(`${baseUrl}/logs?${q.toString()}`, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(this.transport.timeoutMs), retry(this.transport.retryCount)),
    );
  }

  rotateToken(baseUrl: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/rotate-token`, {}, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(this.transport.timeoutMs)),
    );
  }

  getMetrics(baseUrl: string, token?: string): Observable<string> {
    // Metrics endpoint returns raw text, not JSON, so no unwrap.
    return this.transport.http
      .get(`${baseUrl}/metrics`, {
        headers: this.transport.getHeaders(baseUrl, token).headers,
        responseType: 'text',
      })
      .pipe(timeout(this.transport.timeoutMs));
  }

  getClassroomCards(baseUrl: string, filters: Record<string, string> = {}, token?: string): Observable<any> {
    const query = new URLSearchParams(filters).toString();
    return this.transport.unwrap(
      this.transport.http
        .get(`${baseUrl}/api/classroom/cards${query ? `?${query}` : ''}`, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(this.transport.timeoutMs)),
    );
  }

  updateClassroomCard(baseUrl: string, cardId: string, status: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/api/classroom/cards/${encodeURIComponent(cardId)}/status`, { status }, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(this.transport.timeoutMs)),
    );
  }

  exportClassroomWorkflow(baseUrl: string, cardId: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/api/classroom/cards/${encodeURIComponent(cardId)}/export`, {}, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(this.transport.timeoutMs)),
    );
  }
}
