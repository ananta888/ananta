import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { type Observable, map } from 'rxjs';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import type { TeamTemplate, TeamTemplateCatalogResponse } from './caseflow-team-builder.models';

/** One role as the catalog holds it, before a person names an agent after it. */
export interface CatalogRole {
  readonly id: string;
  readonly name: string;
  readonly description?: string;
}

/** Reads the two lists the builder offers: what to start from, and who exists. */
@Injectable({ providedIn: 'root' })
export class CaseFlowTeamBuilderService {
  private readonly http = inject(HttpClient);
  private readonly directory = inject(AgentDirectoryService);

  private get baseUrl(): string {
    return this.directory.list().find(agent => agent.role === 'hub')?.url ?? '';
  }

  listTemplates(): Observable<readonly TeamTemplate[]> {
    return this.http
      .get<{ data?: TeamTemplateCatalogResponse }>(`${this.baseUrl}/teams/templates`)
      .pipe(map(response => response?.data?.templates ?? []));
  }

  /**
   * Every role the catalog knows, for adding an agent a template did not
   * bring. Roles are read live so this list can never drift from the one
   * people edit under Teams.
   */
  listRoles(): Observable<readonly CatalogRole[]> {
    return this.http
      .get<{ data?: readonly CatalogRole[] }>(`${this.baseUrl}/teams/roles`)
      .pipe(map(response => (response?.data ?? []).filter(role => Boolean(role?.id && role?.name))));
  }
}
