import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, timeout, retry, map } from 'rxjs';
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

  private unwrapResponse<T>(obs: Observable<T>): Observable<T> {
    return obs.pipe(
      map((response: any) => {
        if (response && typeof response === 'object' && 'data' in response && 'status' in response) {
          return response.data;
        }
        return response;
      })
    );
  }

  health(baseUrl: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.get(`${baseUrl}/health`, this.getHeaders(baseUrl, token)).pipe(timeout(5000), retry(this.retryCount)));
  }
  ready(baseUrl: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.get(`${baseUrl}/ready`, this.getHeaders(baseUrl, token)).pipe(timeout(5000), retry(this.retryCount)));
  }
  getConfig(baseUrl: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.get(`${baseUrl}/config`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }
  setConfig(baseUrl: string, cfg: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/config`, cfg, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  propose(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/step/propose`, body, this.getHeaders(baseUrl, token)).pipe(timeout(60000))); // LLM calls take longer
  }
  execute(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/step/execute`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
  }
  logs(baseUrl: string, limit = 200, taskId?: string, token?: string): Observable<any> {
    const q = new URLSearchParams({ limit: String(limit), ...(taskId ? { task_id: taskId } : {}) });
    return this.unwrapResponse(this.http.get(`${baseUrl}/logs?${q.toString()}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }
  rotateToken(baseUrl: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.post(`${baseUrl}/rotate-token`, {}, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
  }
  getMetrics(baseUrl: string, token?: string): Observable<string> {
    // Metrics endpoint returns raw text, not JSON, so no unwrapping needed
    return this.http.get(`${baseUrl}/metrics`, {
      headers: this.getHeaders(baseUrl, token).headers,
      responseType: 'text'
    }).pipe(timeout(this.timeoutMs));
  }
  llmGenerate(
    baseUrl: string,
    prompt: string,
    config?: any,
    token?: string,
    options?: {
      history?: Array<{ role: string; content: string }>;
      context?: any;
      tool_calls?: any[];
      confirm_tool_calls?: boolean;
    }
  ): Observable<any> {
    const body: any = { prompt, config };
    if (options) {
      if (options.history) body.history = options.history;
      if (options.context) body.context = options.context;
      if (options.tool_calls) body.tool_calls = options.tool_calls;
      if (options.confirm_tool_calls) body.confirm_tool_calls = options.confirm_tool_calls;
    }
    return this.unwrapResponse(this.http.post(`${baseUrl}/llm/generate`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
  }

  sgptExecute(
    baseUrl: string,
    prompt: string,
    options: string[] = [],
    token?: string,
    useHybridContext = false
  ): Observable<any> {
    const body = { prompt, options, use_hybrid_context: useHybridContext };
    return this.unwrapResponse(this.http.post(`${baseUrl}/api/sgpt/execute`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
  }

  sgptContext(baseUrl: string, query: string, token?: string, includeContextText = true): Observable<any> {
    const body = { query, include_context_text: includeContextText };
    return this.unwrapResponse(this.http.post(`${baseUrl}/api/sgpt/context`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
  }

  sgptSource(baseUrl: string, sourcePath: string, token?: string): Observable<any> {
    const body = { source_path: sourcePath };
    return this.unwrapResponse(this.http.post(`${baseUrl}/api/sgpt/source`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
  }

  getLlmHistory(baseUrl: string, token?: string): Observable<any> {
    return this.unwrapResponse(this.http.get(`${baseUrl}/llm/history`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
  }
}
