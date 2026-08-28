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
  | 'qwen_code'
  | 'gemini_cli'
  | 'copilot_cli'
  | 'cline'
  | 'kilo_code'
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

  /** Hub-authorized install/status operation for one registered Worker. */
  backendProvision(
    baseUrl: string,
    backendId: string,
    body: { worker_url: string; action: 'status' | 'install' },
    token?: string,
  ): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(
          `${baseUrl}/api/sgpt/backends/${encodeURIComponent(backendId)}/provision`,
          body,
          this.transport.getHeaders(baseUrl, token),
        )
        .pipe(timeout(body.action === 'install' ? 620000 : 60000)),
    );
  }

  /** Hub-routed management action on one selected Worker. */
  backendWorkerAction(
    baseUrl: string,
    backendId: string,
    body: {
      worker_name: string;
      action:
        | 'diagnose'
        | 'test_run'
        | 'account_status'
        | 'login_start'
        | 'login_status'
        | 'login_input'
        | 'login_cancel';
      prompt?: string;
      model?: string;
      timeout?: number;
      session_id?: string;
      value?: string;
    },
    token?: string,
  ): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(
          `${baseUrl}/api/sgpt/backends/${encodeURIComponent(backendId)}/worker-action`,
          body,
          this.transport.getHeaders(baseUrl, token),
        )
        .pipe(timeout(body.action === 'test_run' ? 320000 : 65000)),
    );
  }

  /** write_armed: schreibender Claude-Run im isolierten Workspace, liefert Diff-Artefakt. */
  claudeWriteArmedRun(baseUrl: string, body: { prompt: string; workdir: string; model?: string; timeout?: number }, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/api/sgpt/backends/claude_code/write-armed-run`, body, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(3620000)),
    );
  }

  /** Diff-Apply nach Review: wendet einen geprueften write_armed-Diff auf das Original an. */
  claudeApplyDiff(baseUrl: string, body: { diff: string; workdir: string }, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/api/sgpt/backends/claude_code/apply-diff`, body, this.transport.getHeaders(baseUrl, token))
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
