import { Injectable, inject } from '@angular/core';
import { Observable, retry, timeout } from 'rxjs';

import { AgentApiTransport } from './agent-api-transport.service';

export interface LlmGenerateOptions {
  history?: Array<{ role: string; content: string }>;
  context?: unknown;
  tool_calls?: unknown[];
  confirm_tool_calls?: boolean;
}

/** LLM-Generation und Historie. */
@Injectable({ providedIn: 'root' })
export class LlmApiClient {
  private transport = inject(AgentApiTransport);

  generate(
    baseUrl: string,
    prompt: string,
    config?: unknown,
    token?: string,
    options?: LlmGenerateOptions,
  ): Observable<any> {
    const body: Record<string, unknown> = { prompt, config };
    if (options) {
      if (options.history) body.history = options.history;
      if (options.context) body.context = options.context;
      if (options.tool_calls) body.tool_calls = options.tool_calls;
      if (options.confirm_tool_calls) body.confirm_tool_calls = options.confirm_tool_calls;
    }
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/llm/generate`, body, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(120000)),
    );
  }

  history(baseUrl: string, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .get(`${baseUrl}/llm/history`, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(this.transport.timeoutMs), retry(this.transport.retryCount)),
    );
  }
}
