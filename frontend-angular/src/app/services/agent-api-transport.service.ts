import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiResponse, unwrapApiResponse } from './api-envelope';
import { AgentDirectoryService } from './agent-directory.service';
import { resolveAgentForUrl } from './auth-target.resolver';

/**
 * Gemeinsame Transport-Helfer für die fachlichen API-Clients.
 *
 * Ersetzt die privaten Helfer im bisherigen AgentApiService-Monolith und wird
 * von SystemApiClient, TaskApiClient, LlmApiClient, SgptApiClient und
 * WorkspaceApiClient geteilt.
 */
@Injectable({ providedIn: 'root' })
export class AgentApiTransport {
  readonly http = inject(HttpClient);
  private dir = inject(AgentDirectoryService);

  readonly timeoutMs = 15000;
  readonly retryCount = 2;

  getHeaders(baseUrl: string, token?: string): { headers: HttpHeaders } {
    let headers = new HttpHeaders();
    const agents = this.dir.list();
    const agent = resolveAgentForUrl(agents, baseUrl);

    // Raw Agent-Shared-Secrets werden nie als Bearer-Token gesendet;
    // der AuthInterceptor baut daraus kurzlebige JWTs.
    if (token && agent?.token && token === agent.token) {
      token = undefined;
    }

    // Implicit Hub user authentication is owned by AuthInterceptor so Hub
    // requests retain the shared refresh-and-retry policy.
    if (token) {
      headers = headers.set('Authorization', `Bearer ${token}`);
    }
    return { headers };
  }

  unwrap<T>(obs: Observable<ApiResponse<T>>): Observable<T> {
    return unwrapApiResponse<T>(obs);
  }
}
