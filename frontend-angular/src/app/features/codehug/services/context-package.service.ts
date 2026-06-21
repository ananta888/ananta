import { Injectable, inject } from '@angular/core';
import { Observable, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import {
  ChContextPackageCreateRequest,
  ChContextPackageReadModel,
  ChContextPackageUpdateRequest,
  ChSensitiveFileDecision,
  ChServiceError,
  DEFAULT_SENSITIVE_FILE_PATTERNS,
} from '../models/codehug.models';

/**
 * ContextPackageService — CRUD fuer Kontextpakete ueber die Hub-API.
 *
 * SOLID: SRP — nur Kontextpaket-Persistenz. Sensitive-File-Erkennung ist
 * Teil des Service (clientseitige Vorpruefung + serverseitige Authoritaet).
 *
 * Persistierung erfolgt im Backend (nicht localStorage). Pro Projekt, mit
 * Versionierung.
 */
@Injectable({ providedIn: 'root' })
export class ContextPackageService {
  private readonly hub = inject(HubApiCoreService);
  private readonly dir = inject(AgentDirectoryService);

  /** Aktive Sensitive-Patterns. Standardmaessig DEFAULT_SENSITIVE_FILE_PATTERNS. */
  private sensitivePatterns: string[] = [...DEFAULT_SENSITIVE_FILE_PATTERNS];

  /** Setzt die aktiven Sensitive-Patterns (z.B. aus Policy geladen). */
  setSensitivePatterns(patterns: readonly string[]): void {
    this.sensitivePatterns = patterns.length > 0 ? [...patterns] : [...DEFAULT_SENSITIVE_FILE_PATTERNS];
  }

  /** Liefert die aktiven Sensitive-Patterns. */
  getSensitivePatterns(): readonly string[] {
    return this.sensitivePatterns;
  }

  /**
   * Prueft eine Liste von Datei-Pfaden gegen die aktiven Sensitive-Patterns.
   * Liefert pro Pfad eine Entscheidung.
   */
  classifySensitiveFiles(filePaths: readonly string[]): ChSensitiveFileDecision[] {
    return filePaths.map(p => {
      const matched = this.sensitivePatterns.find(pat => this.matchGlob(p, pat));
      return {
        filePath: p,
        matchedPattern: matched ?? null,
        decision: matched ? 'requires-confirmation' : 'auto-exclude',
      };
    });
  }

  /** Erstellt ein neues Kontextpaket. */
  create(request: ChContextPackageCreateRequest): Observable<ChContextPackageReadModel> {
    // Sensitive-Files blockieren, wenn nicht explizit freigegeben
    const decisions = this.classifySensitiveFiles(request.filePaths);
    const blocked = decisions.filter(d => d.decision === 'requires-confirmation');
    if (blocked.length > 0) {
      throw new ChServiceError(
        'validation_error',
        `Sensitive Dateien muessen explizit freigegeben werden: ${blocked.map(b => b.filePath).join(', ')}`,
      );
    }
    const url = `${this.hubUrl()}/api/codehug/context-packages`;
    return this.hub.post<ChContextPackageReadModel>(url, request, this.hubUrl()).pipe(
      map(r => this.normalize(r)),
      catchError(err => throwError(() => this.toChError(err, 'create'))),
    );
  }

  /** Aktualisiert ein bestehendes Kontextpaket (erhoeht version). */
  update(id: string, request: ChContextPackageUpdateRequest): Observable<ChContextPackageReadModel> {
    const url = `${this.hubUrl()}/api/codehug/context-packages/${encodeURIComponent(id)}`;
    return this.hub.patch<ChContextPackageReadModel>(url, request, this.hubUrl()).pipe(
      map(r => this.normalize(r)),
      catchError(err => throwError(() => this.toChError(err, 'update'))),
    );
  }

  /** Laedt ein Kontextpaket per ID. */
  get(id: string): Observable<ChContextPackageReadModel> {
    const url = `${this.hubUrl()}/api/codehug/context-packages/${encodeURIComponent(id)}`;
    return this.hub.get<ChContextPackageReadModel>(url, this.hubUrl()).pipe(
      map(r => this.normalize(r)),
      catchError(err => throwError(() => this.toChError(err, 'get'))),
    );
  }

  /** Liste aller Pakete eines Projekts. */
  listForProject(projectId: string): Observable<ChContextPackageReadModel[]> {
    const url = `${this.hubUrl()}/api/codehug/context-packages?project_id=${encodeURIComponent(projectId)}`;
    return this.hub.get<{ packages: any[] } | ChContextPackageReadModel[]>(url, this.hubUrl()).pipe(
      map(resp => {
        const arr = Array.isArray(resp) ? resp : (resp.packages ?? []);
        return arr.map(r => this.normalize(r));
      }),
      catchError(err => throwError(() => this.toChError(err, 'listForProject'))),
    );
  }

  /** Loescht ein Kontextpaket. */
  delete(id: string): Observable<void> {
    const url = `${this.hubUrl()}/api/codehug/context-packages/${encodeURIComponent(id)}`;
    return this.hub.delete<void>(url, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'delete'))),
    );
  }

  /**
   * Exportiert ein Kontextpaket als JSON.
   *
   * Sensitive-Inhalte werden gewarnt, aber NICHT stillschweigend entfernt.
   * Aufrufer muss die Warnung explizit akzeptieren (confirmExport=true).
   */
  exportAsJson(id: string, confirmExport: boolean): Observable<Blob> {
    if (!confirmExport) {
      throw new ChServiceError(
        'validation_error',
        'Export sensitive Inhalte erfordert explizite Bestaetigung (confirmExport=true).',
      );
    }
    const url = `${this.hubUrl()}/api/codehug/context-packages/${encodeURIComponent(id)}/export?format=json`;
    return this.hub.get<Blob>(url, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'exportAsJson'))),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Helpers
  // ─────────────────────────────────────────────────────────────────────────

  private hubUrl(): string {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      throw new ChServiceError('not_found', 'Kein Hub-Agent im AgentDirectory registriert.');
    }
    return hub.url;
  }

  private normalize(r: any): ChContextPackageReadModel {
    return {
      id: r.id ?? '',
      projectId: r.project_id ?? r.projectId ?? '',
      name: r.name ?? '',
      description: r.description,
      version: r.version ?? 1,
      createdAt: r.created_at ?? r.createdAt ?? 0,
      updatedAt: r.updated_at ?? r.updatedAt ?? 0,
      filePaths: r.file_paths ?? r.filePaths ?? [],
      symbolIds: r.symbol_ids ?? r.symbolIds ?? [],
      contextGroups: r.context_groups ?? r.contextGroups,
      reasons: r.reasons ?? {},
      estimatedTokenCount: r.estimated_token_count ?? r.estimatedTokenCount ?? 0,
      taskDescription: r.task_description ?? r.taskDescription,
      policySnapshotId: r.policy_snapshot_id ?? r.policySnapshotId,
    };
  }

  /**
   * Minimale Glob-Match-Implementation: unterstuetzt '*' (beliebige Zeichen in einem Pfad-Segment) und '**' (rekursiv).
   */
  private matchGlob(path: string, pattern: string): boolean {
    // Normalize
    const normPath = path.replace(/\\/g, '/');
    const normPat = pattern.replace(/\\/g, '/');

    // Exact match
    if (normPath === normPat) return true;

    // ** am Anfang -> rekursiv
    if (normPat.startsWith('**/')) {
      const rest = normPat.slice(3);
      if (normPath.endsWith(rest)) return true;
      const segments = normPath.split('/');
      for (let i = 0; i < segments.length; i++) {
        const sub = segments.slice(i).join('/');
        if (this.matchGlob(sub, rest)) return true;
      }
      return false;
    }
    // ** am Ende -> enthaelt prefix
    if (normPat.endsWith('/**')) {
      const prefix = normPat.slice(0, -3);
      return normPath.startsWith(prefix + '/') || normPath === prefix;
    }

    // * als Wildcard (single segment)
    const regex = new RegExp('^' + normPat
      .replace(/[.+^${}()|[\]\\]/g, '\\$&')
      .replace(/\*/g, '[^/]*') + '$');
    return regex.test(normPath);
  }

  private toChError(err: unknown, operation: string): ChServiceError {
    let code: any = 'unknown';
    let message = `${operation} failed`;
    if (err instanceof Error) {
      message = `${operation}: ${err.message}`;
      if (err.name === 'TimeoutError') code = 'timeout';
    }
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