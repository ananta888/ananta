import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { type Observable, map } from 'rxjs';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import {
  type TaskView,
  type TraceEntry,
  type WorkScope,
  narrowToScope,
  scopeQuery,
  toTaskViews,
  toTraceEntries,
} from './caseflow-work.models';

/** How much of each list is worth putting in front of a person at once. */
const TASK_LIMIT = 200;
const TRACE_LIMIT = 200;

/**
 * The work the Hub knows about, scoped to one level.
 *
 * Both reads go to the task management blueprint, which is registered at the
 * root rather than under /api — the same trap the team catalog fell into, so
 * the paths are pinned by tests here too.
 */
@Injectable({ providedIn: 'root' })
export class CaseFlowWorkService {
  private readonly http = inject(HttpClient);
  private readonly directory = inject(AgentDirectoryService);

  private get baseUrl(): string {
    return this.directory.list().find(agent => agent.role === 'hub')?.url ?? '';
  }

  /**
   * The tasks at one level.
   *
   * The Hub's task list filters by agent only, so every other level is
   * narrowed here on the identity each task already carries. Narrowing after
   * the fact is honest — the alternative is showing one team the whole
   * organisation's work under its own name.
   */
  tasks(scope: Readonly<WorkScope>): Observable<readonly TaskView[]> {
    let params = new HttpParams().set('limit', String(TASK_LIMIT));
    const query = scopeQuery(scope);
    if (query['agent']) params = params.set('agent', query['agent']);
    return this.http.get<unknown>(`${this.baseUrl}/tasks`, { params }).pipe(
      map(response => toTaskViews(unwrap(response))),
      map(tasks => narrowToScope(tasks, scope)),
    );
  }

  trace(scope: Readonly<WorkScope>): Observable<readonly TraceEntry[]> {
    let params = new HttpParams().set('limit', String(TRACE_LIMIT));
    for (const [key, value] of Object.entries(scopeQuery(scope))) {
      params = params.set(key, value);
    }
    return this.http
      .get<unknown>(`${this.baseUrl}/tasks/timeline`, { params })
      .pipe(map(response => toTraceEntries(unwrap(response))));
  }
}

/** The Hub wraps every payload in a status envelope; both shapes are accepted. */
function unwrap(response: unknown): unknown {
  if (response && typeof response === 'object' && 'data' in response) {
    return (response as { data: unknown }).data;
  }
  return response;
}
