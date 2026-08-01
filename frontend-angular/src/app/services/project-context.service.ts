import { Injectable, computed, inject, signal } from '@angular/core';
import { NavigationEnd, Router } from '@angular/router';
import {
  Observable,
  catchError,
  distinctUntilChanged,
  filter,
  finalize,
  map,
  of,
  shareReplay,
  tap,
  throwError,
} from 'rxjs';

import type {
  ProjectContextSnapshot,
  ProjectCreateRequest,
  ProjectSummary,
} from '../models/project-context.model';
import { PROJECT_CATALOG } from './project-catalog.port';
import { UserAuthService } from './user-auth.service';

const PROJECT_QUERY_KEY = 'projectId';
const LEGACY_PROJECT_QUERY_KEY = 'project_id';

@Injectable({ providedIn: 'root' })
export class ProjectContextService {
  private readonly router = inject(Router);
  private readonly auth = inject(UserAuthService);
  private readonly catalog = inject(PROJECT_CATALOG);
  private loadInFlight$: Observable<ProjectContextSnapshot> | null = null;
  private identity = '';

  readonly projects = signal<readonly ProjectSummary[]>([]);
  readonly selectedProjectId = signal('');
  readonly loading = signal(false);
  readonly ready = signal(false);
  readonly error = signal('');

  readonly selectedProject = computed(() =>
    this.projects().find((project) => project.id === this.selectedProjectId()) ?? null,
  );
  readonly hasProject = computed(() => this.selectedProject() !== null);

  constructor() {
    this.auth.user$
      .pipe(
        map((payload) => identityKey(payload)),
        distinctUntilChanged(),
      )
      .subscribe((identity) => {
        this.identity = identity;
        this.reset();
      });
    this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => this.adoptRouteSelection());
  }

  snapshot(): ProjectContextSnapshot {
    return {
      projects: this.projects(),
      selectedProjectId: this.selectedProjectId(),
      loading: this.loading(),
      ready: this.ready(),
      error: this.error(),
    };
  }

  ensureLoaded(force = false): Observable<ProjectContextSnapshot> {
    if (!force && this.ready()) {
      return of(this.snapshot());
    }
    if (!force && this.loadInFlight$) {
      return this.loadInFlight$;
    }
    this.loading.set(true);
    this.error.set('');
    const operation = this.catalog.listProjects().pipe(
      tap((projects) => {
        this.projects.set([...projects]);
        this.ready.set(true);
        this.resolveInitialSelection();
      }),
      map(() => this.snapshot()),
      catchError((error) => {
        this.projects.set([]);
        this.selectedProjectId.set('');
        this.ready.set(false);
        this.error.set('Projekte konnten nicht geladen werden.');
        return throwError(() => error);
      }),
      finalize(() => {
        this.loading.set(false);
        this.loadInFlight$ = null;
      }),
      shareReplay({ bufferSize: 1, refCount: false }),
    );
    this.loadInFlight$ = operation;
    return operation;
  }

  selectProject(projectId: string, synchronizeUrl = true): boolean {
    const normalized = projectId.trim();
    const project = this.projects().find(
      (candidate) => candidate.id === normalized && candidate.status === 'active',
    );
    if (!project) {
      this.selectedProjectId.set('');
      this.error.set('Das ausgewaehlte Projekt ist nicht aktiv oder nicht verfuegbar.');
      return false;
    }
    this.selectedProjectId.set(project.id);
    this.error.set('');
    const key = this.storageKey();
    if (key) {
      localStorage.setItem(key, project.id);
    }
    if (synchronizeUrl) {
      void this.synchronizeUrl(project.id);
    }
    return true;
  }

  createProject(request: ProjectCreateRequest): Observable<ProjectSummary> {
    const name = request.name.trim();
    const description = request.description?.trim();
    if (!name || name.length > 160 || (description?.length ?? 0) > 2000) {
      return throwError(() => new Error('project_create_invalid'));
    }
    this.loading.set(true);
    this.error.set('');
    return this.catalog.createProject({
      name,
      ...(description ? { description } : {}),
    }).pipe(
      tap((project) => {
        this.projects.update((projects) => [
          ...projects.filter((candidate) => candidate.id !== project.id),
          project,
        ].sort((left, right) => left.name.localeCompare(right.name)));
        this.ready.set(true);
        this.selectProject(project.id);
      }),
      catchError((error) => {
        this.error.set('Das Projekt konnte nicht erstellt werden.');
        return throwError(() => error);
      }),
      finalize(() => this.loading.set(false)),
    );
  }

  urlWithProject(url: string, projectId = this.selectedProjectId()): string {
    const tree = this.router.parseUrl(url);
    if (projectId) {
      tree.queryParams[PROJECT_QUERY_KEY] = projectId;
      delete tree.queryParams[LEGACY_PROJECT_QUERY_KEY];
    }
    return this.router.serializeUrl(tree);
  }

  private reset(): void {
    this.projects.set([]);
    this.selectedProjectId.set('');
    this.loading.set(false);
    this.ready.set(false);
    this.error.set('');
    this.loadInFlight$ = null;
  }

  private resolveInitialSelection(): void {
    const requested = this.routeProjectId();
    if (requested) {
      if (!this.selectProject(requested, false)) {
        this.error.set('Der Projektkontext der URL ist nicht verfuegbar.');
      }
      return;
    }
    const persisted = this.storageKey()
      ? localStorage.getItem(this.storageKey()!)?.trim() ?? ''
      : '';
    if (persisted && this.selectProject(persisted)) {
      return;
    }
    const active = this.projects().filter((project) => project.status === 'active');
    if (active.length === 1) {
      this.selectProject(active[0].id);
      return;
    }
    this.selectedProjectId.set('');
  }

  private adoptRouteSelection(): void {
    if (!this.ready()) {
      return;
    }
    const requested = this.routeProjectId();
    if (requested && requested !== this.selectedProjectId()) {
      this.selectProject(requested, false);
    }
  }

  private routeProjectId(): string {
    const tree = this.router.parseUrl(this.router.url || '/');
    return String(
      tree.queryParams[PROJECT_QUERY_KEY]
        ?? tree.queryParams[LEGACY_PROJECT_QUERY_KEY]
        ?? '',
    ).trim();
  }

  private synchronizeUrl(projectId: string): Promise<boolean> {
    const tree = this.router.parseUrl(this.router.url || '/');
    tree.queryParams[PROJECT_QUERY_KEY] = projectId;
    delete tree.queryParams[LEGACY_PROJECT_QUERY_KEY];
    return this.router.navigateByUrl(tree, { replaceUrl: true });
  }

  private storageKey(): string | null {
    return this.identity
      ? `ananta.project.selected.${encodeURIComponent(this.identity)}`
      : null;
  }
}

function identityKey(payload: unknown): string {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return '';
  }
  const value = payload as Record<string, unknown>;
  const tenant = String(value['tenant_id'] ?? 'default').trim() || 'default';
  const user = String(
    value['sub'] ?? value['user_id'] ?? value['username'] ?? '',
  ).trim();
  return user ? `${tenant}:${user}` : '';
}
