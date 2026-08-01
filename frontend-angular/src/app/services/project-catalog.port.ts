import { InjectionToken, inject } from '@angular/core';
import type { Observable } from 'rxjs';

import type { ProjectCreateRequest, ProjectSummary } from '../models/project-context.model';
import { ProjectCatalogHubAdapter } from './project-catalog-hub.adapter';

export interface ProjectCatalogPort {
  listProjects(): Observable<readonly ProjectSummary[]>;
  createProject(request: ProjectCreateRequest): Observable<ProjectSummary>;
}

export const PROJECT_CATALOG = new InjectionToken<ProjectCatalogPort>('PROJECT_CATALOG', {
  providedIn: 'root',
  factory: () => inject(ProjectCatalogHubAdapter),
});
