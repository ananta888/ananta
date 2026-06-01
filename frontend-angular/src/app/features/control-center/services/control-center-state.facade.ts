import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, catchError, of } from 'rxjs';
import { Subscription } from 'rxjs';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import {
  CcProjectReadModel,
  CcPolicyDecisionReadModel,
  CcSessionReadModel,
  CcTaskReadModel,
  CcToolCallReadModel,
  CcWorkerReadModel,
  HubControlCenterApiClient,
} from './hub-control-center-api.client';
import { ControlCenterEventStreamService } from './control-center-event-stream.service';

@Injectable({ providedIn: 'root' })
export class ControlCenterStateFacade {
  private api = inject(HubControlCenterApiClient);
  private directory = inject(AgentDirectoryService);
  private stream = inject(ControlCenterEventStreamService);

  readonly projects$ = new BehaviorSubject<CcProjectReadModel[]>([]);
  readonly tasks$ = new BehaviorSubject<CcTaskReadModel[]>([]);
  readonly sessions$ = new BehaviorSubject<CcSessionReadModel[]>([]);
  readonly workers$ = new BehaviorSubject<CcWorkerReadModel[]>([]);
  readonly toolCallsBySessionId$ = new BehaviorSubject<Record<string, CcToolCallReadModel[]>>({});
  readonly policyDecisions$ = new BehaviorSubject<CcPolicyDecisionReadModel[]>([]);
  readonly taskVerificationById$ = new BehaviorSubject<Record<string, { status: string; test_count: number; passed_count: number; failed_count: number }>>({});
  readonly selectedProjectId$ = new BehaviorSubject<string>('');
  readonly loading$ = new BehaviorSubject<boolean>(false);
  readonly error$ = new BehaviorSubject<string>('');
  private lastEventTsByEntity = new Map<string, number>();
  private eventSub: Subscription | null = null;
  private connectingEvents = false;

  constructor() {
    const initial = localStorage.getItem('ananta.cc.selectedProjectId') || '';
    this.selectedProjectId$.next(initial);
  }

  hubBaseUrl(): string | null {
    const hub = this.directory.list().find((a) => a.role === 'hub') || this.directory.list().find((a) => a.name === 'hub');
    return hub?.url || null;
  }

  loadProjects(): void {
    const baseUrl = this.hubBaseUrl();
    if (!baseUrl) {
      this.error$.next('Kein Hub konfiguriert');
      return;
    }
    this.loading$.next(true);
    this.api.listProjects(baseUrl).pipe(
      catchError(() => {
        this.error$.next('Projekte konnten nicht geladen werden');
        return of({ items: [], count: 0 });
      }),
    ).subscribe((res) => {
      this.projects$.next(res.items || []);
      const current = this.selectedProjectId$.value;
      const fallback = (res.items || [])[0]?.id || '';
      const selected = (res.items || []).some((p) => p.id === current) ? current : fallback;
      this.selectProject(selected, false);
      this.loading$.next(false);
    });
  }

  selectProject(projectId: string, reload = true): void {
    this.selectedProjectId$.next(projectId || '');
    localStorage.setItem('ananta.cc.selectedProjectId', projectId || '');
    if (reload) {
      this.loadTasks();
      this.loadSessions();
    }
  }

  loadTasks(): void {
    const baseUrl = this.hubBaseUrl();
    const projectId = this.selectedProjectId$.value;
    if (!baseUrl || !projectId) {
      this.tasks$.next([]);
      return;
    }
    this.api.listProjectTasks(baseUrl, projectId).pipe(
      catchError(() => {
        this.error$.next('Tasks konnten nicht geladen werden');
        return of({ items: [], count: 0 });
      }),
    ).subscribe((res) => this.tasks$.next(res.items || []));
  }

  loadSessions(taskId?: string): void {
    const baseUrl = this.hubBaseUrl();
    if (!baseUrl) {
      this.sessions$.next([]);
      return;
    }
    this.api.listSessions(baseUrl, taskId).pipe(
      catchError(() => {
        this.error$.next('Sessions konnten nicht geladen werden');
        return of({ items: [], count: 0 });
      }),
    ).subscribe((res) => this.sessions$.next(res.items || []));
  }

  loadWorkers(): void {
    const baseUrl = this.hubBaseUrl();
    if (!baseUrl) {
      this.workers$.next([]);
      return;
    }
    this.api.listWorkers(baseUrl).pipe(
      catchError(() => {
        this.error$.next('Workers konnten nicht geladen werden');
        return of({ items: [], count: 0 });
      }),
    ).subscribe((res) => this.workers$.next(res.items || []));
  }

  connectEvents(): void {
    const base = this.hubBaseUrl();
    if (!base || this.connectingEvents) return;
    this.connectingEvents = true;
    this.api.createEventStreamToken(base, { project_id: this.selectedProjectId$.value || undefined }).pipe(
      catchError(() => of(null)),
    ).subscribe((tokenRes) => {
      const streamToken = String(tokenRes?.stream_token || tokenRes?.token || '');
      if (!streamToken) {
        this.connectingEvents = false;
        return;
      }
      this.stream.connect(`${base}/api/events/stream`, streamToken);
      if (!this.eventSub) {
        this.eventSub = this.stream.lastEventObject$.subscribe((evt) => {
          if (!evt) return;
          this.mergeEvent(evt);
        });
      }
      this.connectingEvents = false;
    });
  }

  disconnectEvents(): void {
    this.stream.disconnect();
    if (this.eventSub) {
      this.eventSub.unsubscribe();
      this.eventSub = null;
    }
    this.connectingEvents = false;
  }

  loadPolicyDecisions(sessionId: string): void {
    const baseUrl = this.hubBaseUrl();
    if (!baseUrl || !sessionId) {
      this.policyDecisions$.next([]);
      return;
    }
    this.api.listSessionPolicyDecisions(baseUrl, sessionId).pipe(
      catchError(() => {
        this.error$.next('Policy Decisions konnten nicht geladen werden');
        return of({ items: [], count: 0 });
      }),
    ).subscribe((res) => this.policyDecisions$.next(res.items || []));
  }

  loadSessionToolCalls(sessionId: string): void {
    const baseUrl = this.hubBaseUrl();
    if (!baseUrl || !sessionId) return;
    this.api.listSessionToolCalls(baseUrl, sessionId).pipe(
      catchError(() => of({ items: [], count: 0 })),
    ).subscribe((res) => {
      const next = { ...this.toolCallsBySessionId$.value, [sessionId]: res.items || [] };
      this.toolCallsBySessionId$.next(next);
    });
  }

  approveAction(payload: { action_id: string; tool_call_id: string; session_id: string }): ReturnType<HubControlCenterApiClient['approvePolicyAction']> | null {
    const baseUrl = this.hubBaseUrl();
    if (!baseUrl) return null;
    return this.api.approvePolicyAction(baseUrl, { ...payload, scope: 'single_action' });
  }

  loadTaskDetailVerification(taskId: string): void {
    const baseUrl = this.hubBaseUrl();
    if (!baseUrl || !taskId) return;
    this.api.getTaskDetail(baseUrl, taskId).pipe(
      catchError(() => of({ task: {}, verification: {} })),
    ).subscribe((detail) => {
      const raw = (detail.verification || {}) as Record<string, unknown>;
      const current = { ...this.taskVerificationById$.value };
      current[taskId] = {
        status: String(raw.status || 'not_run'),
        test_count: Number(raw.test_count || 0),
        passed_count: Number(raw.passed_count || 0),
        failed_count: Number(raw.failed_count || 0),
      };
      this.taskVerificationById$.next(current);
    });
  }

  private mergeEvent(evt: Record<string, unknown>): void {
    const eventType = String(evt.type || '');
    const payload = (evt.payload || {}) as Record<string, unknown>;
    const ts = Number(evt.timestamp || 0);
    if (eventType === 'task_updated' && payload.id) {
      const id = String(payload.id);
      const key = `task:${id}`;
      const lastTs = this.lastEventTsByEntity.get(key) || 0;
      if (ts < lastTs) return;
      this.lastEventTsByEntity.set(key, ts);
      const next = [...this.tasks$.value];
      const idx = next.findIndex((x) => x.id === id);
      const mapped: CcTaskReadModel = {
        id,
        title: String(payload.title || ''),
        description: String(payload.description || ''),
        status: String(payload.status || 'todo'),
        priority: String(payload.priority || 'Medium'),
        project_id: (payload.project_id as string) || null,
      };
      if (idx >= 0) next[idx] = mapped;
      else next.unshift(mapped);
      this.tasks$.next(next);
    }
  }
}
