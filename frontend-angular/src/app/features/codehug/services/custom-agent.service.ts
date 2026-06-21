import { Injectable, inject } from '@angular/core';
import { Observable, of, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import {
  ChAgentDefinitionReadModel,
  ChAgentDefinitionInput,
  ChAgentRunReadModel,
  ChServiceError,
} from '../models/codehug.models';

/**
 * CustomAgentService — CH-006: Custom Agent-Definitionen speichern/laden/ausfuehren.
 *
 * SOLID: SRP — CRUD + Run fuer Custom Agents. Storage-Hub-tokens uebernehmen.
 *
 * Sicherheit: Aenderungen an Agent-Definitionen erfordern write-Modus (delegiert
 * an PolicyService in der UI, hier nur schluesselreife Checks).
 */
@Injectable({ providedIn: 'root' })
export class CustomAgentService {
  private readonly hub = inject(HubApiCoreService);
  private readonly dir = inject(AgentDirectoryService);

  private hubUrl(): string {
    const h = this.dir.list().find(a => a.role === 'hub');
    if (!h) throw new ChServiceError('not_found', 'Kein Hub-Agent registriert');
    return h.url;
  }

  list(): Observable<ChAgentDefinitionReadModel[]> {
    const url = `${this.hubUrl()}/api/custom-agents`;
    return this.hub.get<ChAgentDefinitionReadModel[]>(url, this.hubUrl()).pipe(
      map(arr => arr ?? []),
      catchError(err => throwError(() => this.toChError(err, 'list'))),
    );
  }

  get(id: string): Observable<ChAgentDefinitionReadModel> {
    const url = `${this.hubUrl()}/api/custom-agents/${encodeURIComponent(id)}`;
    return this.hub.get<ChAgentDefinitionReadModel>(url, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'get'))),
    );
  }

  create(input: ChAgentDefinitionInput): Observable<ChAgentDefinitionReadModel> {
    const url = `${this.hubUrl()}/api/custom-agents`;
    return this.hub.post<ChAgentDefinitionReadModel>(url, input, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'create'))),
    );
  }

  update(id: string, input: ChAgentDefinitionInput): Observable<ChAgentDefinitionReadModel> {
    const url = `${this.hubUrl()}/api/custom-agents/${encodeURIComponent(id)}`;
    return this.hub.put<ChAgentDefinitionReadModel>(url, input, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'update'))),
    );
  }

  remove(id: string): Observable<void> {
    const url = `${this.hubUrl()}/api/custom-agents/${encodeURIComponent(id)}`;
    return this.hub.delete<void>(url, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'remove'))),
    );
  }

  /** Startet einen Run mit einem Custom-Agent. */
  run(agentId: string, prompt: string, context?: string): Observable<ChAgentRunReadModel> {
    const url = `${this.hubUrl()}/api/custom-agents/${encodeURIComponent(agentId)}/run`;
    return this.hub.post<ChAgentRunReadModel>(url, { prompt, context }, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'run'))),
    );
  }

  /** Health-Check. */
  ping(): Observable<boolean> {
    return this.list().pipe(
      map(() => true),
      catchError(() => of(false)),
    );
  }

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