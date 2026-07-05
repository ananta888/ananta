import { Injectable, inject } from '@angular/core';
import { Observable, timeout } from 'rxjs';

import { AgentApiTransport } from './agent-api-transport.service';

export type SgptBackend =
  | 'sgpt'
  | 'codex'
  | 'opencode'
  | 'claude_code'
  | 'aider'
  | 'mistral_code'
  | 'auto';

/** Shell-GPT / Coding-Backend-Endpunkte. */
@Injectable({ providedIn: 'root' })
export class SgptApiClient {
  private transport = inject(AgentApiTransport);

  execute(
    baseUrl: string,
    prompt: string,
    options: string[] = [],
    token?: string,
    useHybridContext = false,
    backend?: SgptBackend,
  ): Observable<any> {
    const body: Record<string, unknown> = { prompt, options, use_hybrid_context: useHybridContext };
    if (backend) body.backend = backend;
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/api/sgpt/execute`, body, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(120000)),
    );
  }

  context(baseUrl: string, query: string, token?: string, includeContextText = true): Observable<any> {
    const body = { query, include_context_text: includeContextText };
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/api/sgpt/context`, body, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(120000)),
    );
  }

  source(baseUrl: string, sourcePath: string, token?: string): Observable<any> {
    const body = { source_path: sourcePath };
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/api/sgpt/source`, body, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(120000)),
    );
  }

  backends(baseUrl: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .get(`${baseUrl}/api/sgpt/backends`, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(120000)),
    );
  }

  /** COMMON-003: Health-Status eines einzelnen CLI-Backends. */
  backendHealth(baseUrl: string, backendId: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .get(`${baseUrl}/api/sgpt/backends/${encodeURIComponent(backendId)}/health`, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(30000)),
    );
  }

  /** COMMON-003: Verify-Command-Diagnose (z.B. `claude --version`). */
  backendDiagnose(baseUrl: string, backendId: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/api/sgpt/backends/${encodeURIComponent(backendId)}/diagnose`, {}, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(60000)),
    );
  }

  /** COMMON-003: read-only Test-Run ueber den regulaeren Run-Pfad. */
  backendTestRun(baseUrl: string, backendId: string, body: { prompt?: string; model?: string; timeout?: number } = {}, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/api/sgpt/backends/${encodeURIComponent(backendId)}/test-run`, body, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(320000)),
    );
  }
}
