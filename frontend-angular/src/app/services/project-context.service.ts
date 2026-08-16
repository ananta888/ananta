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

interface ProjectSelectionLock {
  readonly message: string;
  readonly count: number;
}

@Injectable({ providedIn: 'root' })
export class ProjectContextService {
  private readonly router = inject(Router);
  private readonly auth = inject(UserAuthService);
  private readonly catalog = inject(PROJECT_CATALOG);
  private loadInFlight$: Observable<ProjectContextSnapshot> | null = null;
  private identity = '';
  private readonly selectionLocks = signal<ReadonlyMap<string, ProjectSelectionLock>>(new Map());

  readonly projects = signal<readonly ProjectSummary[]>([]);
  readonly selectedProjectId = signal('');
  readonly loading = signal(false);
  readonly ready = signal(false);
  readonly error = signal('');

  readonly selectedProject = computed(() =>
    this.projects().find((project) => project.id === this.selectedProjectId()) ?? null,
  );
  readonly hasProject = computed(() => this.selectedProject() !== null);
  readonly selectionBlocked = computed(() => this.selectionLocks().size > 0);
  readonly selectionBlockMessage = computed(() => (
    this.selectionLocks().values().next().value?.message
    ?? 'Der Projektwechsel ist während einer laufenden Aktion gesperrt.'
  ));

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
    if (normalized !== this.selectedProjectId() && this.selectionBlocked()) {
      this.error.set(this.selectionBlockMessage());
      return false;
    }
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

  acquireSelectionLock(key: string, message: string): () => void {
    const normalizedKey = String(key || '').trim();
    const normalizedMessage = String(message || '').trim();
    if (!normalizedKey) throw new Error('project_selection_lock_key_required');
    this.selectionLocks.update((current) => {
      const next = new Map(current);
      const existing = next.get(normalizedKey);
      next.set(normalizedKey, {
        message: normalizedMessage || existing?.message || 'Der Projektwechsel ist vorübergehend gesperrt.',
        count: (existing?.count ?? 0) + 1,
      });
      return next;
    });
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.selectionLocks.update((current) => {
        const existing = current.get(normalizedKey);
        if (!existing) return current;
        const next = new Map(current);
        if (existing.count > 1) {
          next.set(normalizedKey, { ...existing, count: existing.count - 1 });
        } else {
          next.delete(normalizedKey);
        }
        return next;
      });
    };
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
    this.selectionLocks.set(new Map());
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
    const selected = this.selectedProjectId();
    if (requested && requested !== selected) {
      const accepted = this.selectProject(requested, false);
      if (!accepted && selected && this.selectionBlocked()) {
        void this.synchronizeUrl(selected);
      }
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
    // While a navigation is in flight -- which is exactly when a route guard
    // resolves the project -- router.url still points at the page being left.
    // On a direct load that is '/', and navigating there cancels the requested
    // route and lands on the workspace redirect instead. Synchronise onto the
    // URL being navigated to, not the one being left.
    const navigation = this.router.getCurrentNavigation();
    const pending = navigation?.finalUrl ?? navigation?.extractedUrl ?? null;
    const tree = this.router.parseUrl(
      pending ? this.router.serializeUrl(pending) : (this.router.url || '/'),
    );
    if (
      String(tree.queryParams[PROJECT_QUERY_KEY] ?? '') === projectId
      && tree.queryParams[LEGACY_PROJECT_QUERY_KEY] === undefined
    ) {
      // Already the requested context: re-navigating would restart the very
      // navigation that is carrying it.
      return Promise.resolve(true);
    }
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
