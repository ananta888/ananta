import { Injectable, inject } from '@angular/core';
import { map, throwError } from 'rxjs';

import type { ProjectCreateRequest, ProjectSummary } from '../models/project-context.model';
import { HubControlCenterApiClient } from '../features/control-center/services/hub-control-center-api.client';
import { AgentDirectoryService } from './agent-directory.service';
import type { ProjectCatalogPort } from './project-catalog.port';

@Injectable({ providedIn: 'root' })
export class ProjectCatalogHubAdapter implements ProjectCatalogPort {
  private readonly api = inject(HubControlCenterApiClient);
  private readonly directory = inject(AgentDirectoryService);

  listProjects() {
    const hubUrl = this.hubUrl();
    return hubUrl
      ? this.api.listProjects(hubUrl).pipe(
          map((page) => page.items.map((project) => ({
            id: project.id,
            name: project.name,
            description: project.description,
            status: project.status ?? (project.is_active ? 'active' as const : 'archived' as const),
            isActive: project.is_active,
            origin: project.origin ?? 'legacy_source_control',
            teamId: project.team_id ?? null,
            version: project.version ?? 1,
            createdAt: project.created_at ?? 0,
            updatedAt: project.updated_at ?? 0,
            archivedAt: project.archived_at ?? null,
          }))),
        )
      : throwError(() => new Error('project_hub_unavailable'));
  }

  createProject(request: ProjectCreateRequest) {
    const hubUrl = this.hubUrl();
    return hubUrl
      ? this.api.createProject(hubUrl, request).pipe(
          map((project) => ({
            id: project.id,
            name: project.name,
            description: project.description,
            status: project.status ?? (project.is_active ? 'active' as const : 'archived' as const),
            isActive: project.is_active,
            origin: project.origin ?? 'native',
            teamId: project.team_id ?? null,
            version: project.version ?? 1,
            createdAt: project.created_at ?? 0,
            updatedAt: project.updated_at ?? 0,
            archivedAt: project.archived_at ?? null,
          })),
        )
      : throwError(() => new Error('project_hub_unavailable'));
  }

  private hubUrl(): string | null {
    const agents = this.directory.list();
    return agents.find((agent) => agent.role === 'hub')?.url
      ?? agents.find((agent) => agent.name === 'hub')?.url
      ?? null;
  }
}
