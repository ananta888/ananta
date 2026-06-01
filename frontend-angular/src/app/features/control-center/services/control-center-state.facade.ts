import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, catchError, of } from 'rxjs';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import {
  CcProjectReadModel,
  CcSessionReadModel,
  CcTaskReadModel,
  CcWorkerReadModel,
  HubControlCenterApiClient,
} from './hub-control-center-api.client';

@Injectable({ providedIn: 'root' })
export class ControlCenterStateFacade {
  private api = inject(HubControlCenterApiClient);
  private directory = inject(AgentDirectoryService);

  readonly projects$ = new BehaviorSubject<CcProjectReadModel[]>([]);
  readonly tasks$ = new BehaviorSubject<CcTaskReadModel[]>([]);
  readonly sessions$ = new BehaviorSubject<CcSessionReadModel[]>([]);
  readonly workers$ = new BehaviorSubject<CcWorkerReadModel[]>([]);
  readonly selectedProjectId$ = new BehaviorSubject<string>('');
  readonly loading$ = new BehaviorSubject<boolean>(false);
  readonly error$ = new BehaviorSubject<string>('');

  constructor() {
    const initial = localStorage.getItem('ananta.cc.selectedProjectId') || '';
    this.selectedProjectId$.next(initial);
  }

  hubBaseUrl(): string | null {
    const hub = this.directory.list().find((a) => a.role === 'hub');
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
}
