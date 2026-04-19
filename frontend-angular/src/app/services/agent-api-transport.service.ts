import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';

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
  private userAuth = inject(UserAuthService);

  readonly timeoutMs = 15000;
  readonly retryCount = 2;

  getHeaders(baseUrl: string, token?: string): { headers: HttpHeaders } {
    let headers = new HttpHeaders();
    const agent = this.dir.list().find(a => baseUrl.startsWith(a.url));

    // Raw Agent-Shared-Secrets werden nie als Bearer-Token gesendet;
    // der AuthInterceptor baut daraus kurzlebige JWTs.
    if (token && agent?.token && token === agent.token) {
      token = undefined;
    }

    if (!token) {
      const hub = this.dir.list().find(a => a.role === 'hub');
      if (hub && baseUrl.startsWith(hub.url) && this.userAuth.token) {
        token = this.userAuth.token;
      }
    }
    if (token) {
      headers = headers.set('Authorization', `Bearer ${token}`);
    }
    return { headers };
  }

  unwrap<T>(obs: Observable<T>): Observable<T> {
    return obs.pipe(
      map((response: any) => {
        if (response && typeof response === 'object' && 'data' in response && 'status' in response) {
          return response.data;
        }
        return response;
      }),
    );
  }
}
