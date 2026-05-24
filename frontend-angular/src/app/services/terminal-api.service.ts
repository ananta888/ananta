import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

export interface TerminalTarget {
  target_type: 'worker' | 'hub' | 'hub_as_worker';
  target_id: string;
  display_name?: string;
  health?: string;
  risk_class?: string;
  capabilities: {
    create: boolean;
    attach: boolean;
    read: boolean;
    write: boolean;
    kill: boolean;
  };
}

export interface TerminalSession {
  id: string;
  target_type: string;
  target_id: string;
  target_display_name?: string;
  status: string;
  read_only: boolean;
  recording_enabled: boolean;
  created_by_username?: string;
  workspace_path?: string;
  goal_id?: string;
  task_id?: string;
  risk_class?: string;
  created_at?: number;
}

@Injectable({ providedIn: 'root' })
export class TerminalApiService {
  private core = inject(HubApiCoreService);

  listTargets(baseUrl: string, token?: string): Observable<{ targets: TerminalTarget[] }> {
    return this.core.get<{ targets: TerminalTarget[] }>(`${baseUrl}/terminal/targets`, baseUrl, token, false);
  }

  listSessions(baseUrl: string, token?: string): Observable<{ sessions: TerminalSession[] }> {
    return this.core.get<{ sessions: TerminalSession[] }>(`${baseUrl}/terminal/sessions`, baseUrl, token, false);
  }

  createSession(baseUrl: string, body: {
    target_type: string;
    target_id: string;
    workspace_path?: string;
    goal_id?: string;
    task_id?: string;
    read_only?: boolean;
  }, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/terminal/sessions`, body, baseUrl, token, false, 15000);
  }

  getSession(baseUrl: string, sessionId: string, token?: string): Observable<{ session: TerminalSession }> {
    return this.core.get<{ session: TerminalSession }>(`${baseUrl}/terminal/sessions/${sessionId}`, baseUrl, token, false);
  }

  getAttachToken(baseUrl: string, sessionId: string, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/terminal/sessions/${sessionId}/attach-token`, {}, baseUrl, token, false, 10000);
  }

  killSession(baseUrl: string, sessionId: string, token?: string): Observable<any> {
    return this.core.delete<any>(`${baseUrl}/terminal/sessions/${sessionId}`, baseUrl, token, 10000);
  }

  toWssUrl(baseUrl: string, attachToken: string): string {
    const url = new URL(baseUrl);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.pathname = '/ws/terminal/session';
    url.search = '';
    url.searchParams.set('attach_token', attachToken);
    return url.toString();
  }
}
