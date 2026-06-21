import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { map, catchError } from 'rxjs/operators';

import { AgentDirectoryService } from '../../../services/agent-directory.service';

export interface AnantaTemplate {
  id: string;
  name: string;
  description: string;
  category: 'scrum' | 'kanban' | 'opencode' | 'system';
  prompt_template: string;
  is_seed: boolean;
}

export interface AnantaWorker {
  url: string;
  name: string;
  role: string;
  status: 'online' | 'offline' | 'degraded';
  worker_roles: string[];
  capabilities: string[];
}

export interface AutopilotStatus {
  running: boolean;
  goal: string;
  team_id: string;
  started_at: number | null;
  tick_count: number;
  dispatched_count: number;
  completed_count: number;
  failed_count: number;
  last_error: string | null;
  effective_security_policy: {
    level: string;
    max_concurrency_cap: number;
    allowed_tool_classes: string[];
  };
  circuit_breakers: {
    open_workers: string[];
    open_count: number;
    failure_streak: Record<string, number>;
  };
}

@Injectable({ providedIn: 'root' })
export class InternalsService {
  private readonly http = inject(HttpClient);
  private readonly dir = inject(AgentDirectoryService);

  private hubUrl(): string {
    const hub = this.dir.list().find(a => a.role === 'hub');
    return hub?.url ?? 'http://127.0.0.1:5000';
  }

  getTemplates(): Observable<AnantaTemplate[]> {
    return this.http.get<any>(`${this.hubUrl()}/templates`).pipe(
      map(resp => {
        const raw: any[] = Array.isArray(resp) ? resp : (resp.data ?? []);
        return raw.map(t => this.normalizeTemplate(t));
      }),
      catchError(() => of([])),
    );
  }

  getWorkers(): Observable<AnantaWorker[]> {
    return this.http.get<any>(`${this.hubUrl()}/api/workers`).pipe(
      map(resp => {
        const raw: any[] = resp?.data?.items ?? resp?.items ?? (Array.isArray(resp) ? resp : []);
        return raw.map(w => ({
          url: w.url ?? '',
          name: w.id ?? w.name ?? 'unknown',
          role: w.role ?? 'worker',
          status: this.mapHealth(w.health ?? w.status),
          worker_roles: Array.isArray(w.worker_roles) ? w.worker_roles : [],
          capabilities: Array.isArray(w.capabilities) ? w.capabilities : [],
        } satisfies AnantaWorker));
      }),
      catchError(() => of([])),
    );
  }

  getAutopilotStatus(): Observable<AutopilotStatus> {
    return this.http.get<any>(`${this.hubUrl()}/tasks/autopilot/status`).pipe(
      map(resp => resp?.data ?? resp),
      catchError(() => of(this.emptyStatus())),
    );
  }

  private normalizeTemplate(t: any): AnantaTemplate {
    const name: string = t.name ?? '';
    let category: AnantaTemplate['category'] = 'system';
    if (name.toLowerCase().includes('scrum') && !name.toLowerCase().includes('opencode')) category = 'scrum';
    else if (name.toLowerCase().includes('opencode')) category = 'opencode';
    else if (name.toLowerCase().includes('kanban')) category = 'kanban';
    return {
      id: t.id ?? '',
      name,
      description: t.description ?? '',
      category,
      prompt_template: t.prompt_template ?? '',
      is_seed: Boolean(t.is_seed),
    };
  }

  private mapHealth(h: string): AnantaWorker['status'] {
    const l = (h ?? '').toLowerCase();
    if (l === 'online' || l === 'healthy') return 'online';
    if (l === 'degraded') return 'degraded';
    return 'offline';
  }

  private emptyStatus(): AutopilotStatus {
    return {
      running: false, goal: '', team_id: '', started_at: null,
      tick_count: 0, dispatched_count: 0, completed_count: 0, failed_count: 0,
      last_error: null,
      effective_security_policy: { level: 'safe', max_concurrency_cap: 1, allowed_tool_classes: [] },
      circuit_breakers: { open_workers: [], open_count: 0, failure_streak: {} },
    };
  }
}
