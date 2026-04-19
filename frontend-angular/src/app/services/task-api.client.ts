import { Injectable, inject } from '@angular/core';
import { Observable, timeout } from 'rxjs';

import { AgentApiTransport } from './agent-api-transport.service';

/** Task-Lifecycle-Endpunkte auf dem Agenten: propose + execute. */
@Injectable({ providedIn: 'root' })
export class TaskApiClient {
  private transport = inject(AgentApiTransport);

  propose(baseUrl: string, body: unknown, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/step/propose`, body, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(60000)),
    );
  }

  execute(baseUrl: string, body: unknown, token?: string): Observable<any> {
    return this.transport.unwrap(
      this.transport.http
        .post(`${baseUrl}/step/execute`, body, this.transport.getHeaders(baseUrl, token))
        .pipe(timeout(120000)),
    );
  }
}
