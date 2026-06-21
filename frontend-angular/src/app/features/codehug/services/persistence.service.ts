import { Injectable, inject, signal } from '@angular/core';
import { Observable, of, throwError } from 'rxjs';
import { catchError, map, tap } from 'rxjs/operators';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import {
  ChWorkspaceReadModel,
  ChWorkspaceInput,
  ChContextSnapshotReadModel,
  ChContextSnapshotInput,
  ChServiceError,
} from '../models/codehug.models';

/**
 * PersistenceService — CH-011: Workspaces, Kontext-Snapshots, Recents.
 *
 * SOLID: SRP — Frontend-Logik fuer Persistenz, Storage via Hub.
 * Wichtig: Snapshots enthalten KEINE Tokens, nur Verweise + Metadaten.
 *
 * Cache: in-memory recent-Liste (LRU, 20 eintraege).
 */
@Injectable({ providedIn: 'root' })
export class PersistenceService {
  private readonly hub = inject(HubApiCoreService);
  private readonly dir = inject(AgentDirectoryService);

  private hubUrl(): string {
    const h = this.dir.list().find(a => a.role === 'hub');
    if (!h) throw new ChServiceError('not_found', 'Kein Hub-Agent registriert');
    return h.url;
  }

  /** Recents (LRU, 20 eintraege). */
  readonly recents = signal<ChWorkspaceReadModel[]>([]);
  private readonly recentLimit = 20;

  // ─────────────────────────────────────────────────────────────────────────
  // Workspaces
  // ─────────────────────────────────────────────────────────────────────────

  listWorkspaces(): Observable<ChWorkspaceReadModel[]> {
    const url = `${this.hubUrl()}/api/codehug/workspaces`;
    return this.hub.get<ChWorkspaceReadModel[]>(url, this.hubUrl()).pipe(
      map(list => list ?? []),
      tap(list => this.refreshRecents(list)),
      catchError(err => throwError(() => this.toChError(err, 'listWorkspaces'))),
    );
  }

  getWorkspace(id: string): Observable<ChWorkspaceReadModel> {
    const url = `${this.hubUrl()}/api/codehug/workspaces/${encodeURIComponent(id)}`;
    return this.hub.get<ChWorkspaceReadModel>(url, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'getWorkspace'))),
    );
  }

  createWorkspace(input: ChWorkspaceInput): Observable<ChWorkspaceReadModel> {
    const url = `${this.hubUrl()}/api/codehug/workspaces`;
    return this.hub.post<ChWorkspaceReadModel>(url, input, this.hubUrl()).pipe(
      tap(w => this.touchRecent(w)),
      catchError(err => throwError(() => this.toChError(err, 'createWorkspace'))),
    );
  }

  updateWorkspace(id: string, input: Partial<ChWorkspaceInput>): Observable<ChWorkspaceReadModel> {
    const url = `${this.hubUrl()}/api/codehug/workspaces/${encodeURIComponent(id)}`;
    return this.hub.patch<ChWorkspaceReadModel>(url, input, this.hubUrl()).pipe(
      tap(w => this.touchRecent(w)),
      catchError(err => throwError(() => this.toChError(err, 'updateWorkspace'))),
    );
  }

  removeWorkspace(id: string): Observable<void> {
    const url = `${this.hubUrl()}/api/codehug/workspaces/${encodeURIComponent(id)}`;
    return this.hub.delete<void>(url, this.hubUrl()).pipe(
      tap(() => this.recents.update(list => list.filter(w => w.id !== id))),
      catchError(err => throwError(() => this.toChError(err, 'removeWorkspace'))),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Kontext-Snapshots
  // ─────────────────────────────────────────────────────────────────────────

  listSnapshots(workspaceId: string): Observable<ChContextSnapshotReadModel[]> {
    const url = `${this.hubUrl()}/api/codehug/workspaces/${encodeURIComponent(workspaceId)}/snapshots`;
    return this.hub.get<ChContextSnapshotReadModel[]>(url, this.hubUrl()).pipe(
      map(list => list ?? []),
      catchError(err => throwError(() => this.toChError(err, 'listSnapshots'))),
    );
  }

  createSnapshot(input: ChContextSnapshotInput): Observable<ChContextSnapshotReadModel> {
    const url = `${this.hubUrl()}/api/codehug/snapshots`;
    return this.hub.post<ChContextSnapshotReadModel>(url, input, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'createSnapshot'))),
    );
  }

  loadSnapshot(snapshotId: string): Observable<ChContextSnapshotReadModel> {
    const url = `${this.hubUrl()}/api/codehug/snapshots/${encodeURIComponent(snapshotId)}`;
    return this.hub.get<ChContextSnapshotReadModel>(url, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'loadSnapshot'))),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Recents (LRU)
  // ─────────────────────────────────────────────────────────────────────────

  private touchRecent(w: ChWorkspaceReadModel): void {
    this.recents.update(list => [w, ...list.filter(x => x.id !== w.id)].slice(0, this.recentLimit));
  }

  private refreshRecents(list: ChWorkspaceReadModel[]): void {
    this.recents.set(list.slice(0, this.recentLimit));
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Errors
  // ─────────────────────────────────────────────────────────────────────────

  private toChError(err: unknown, op: string): ChServiceError {
    let code: any = 'unknown';
    let message = `${op} failed`;
    if (err instanceof Error) message = `${op}: ${err.message}`;
    if (typeof err === 'object' && err !== null) {
      const status = (err as any).status;
      if (status === 401) code = 'unauthorized';
      else if (status === 403) code = 'forbidden';
      else if (status === 404) code = 'not_found';
      else if (status === 422) code = 'validation_error';
      else if (status === 0) code = 'network_error';
      else if (typeof status === 'number' && status >= 500) code = 'backend_error';
    }
    return new ChServiceError(code, message, err);
  }
}