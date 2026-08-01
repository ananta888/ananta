import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { BehaviorSubject, firstValueFrom, of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ProjectSummary } from '../models/project-context.model';
import { PROJECT_CATALOG } from './project-catalog.port';
import { ProjectContextService } from './project-context.service';
import { UserAuthService } from './user-auth.service';

describe('ProjectContextService', () => {
  const user$ = new BehaviorSubject<unknown>({ tenant_id: 'tenant-a', sub: 'user-a' });
  const projects: readonly ProjectSummary[] = [
    project('project-a', 'Alpha'),
    project('project-b', 'Beta'),
  ];
  const listProjects = vi.fn(() => of(projects));
  const createProject = vi.fn(() => of(project('project-c', 'Gamma')));

  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    listProjects.mockReturnValue(of(projects));
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: PROJECT_CATALOG, useValue: { listProjects, createProject } },
        { provide: UserAuthService, useValue: { user$, userPayload: user$.value } },
      ],
    });
  });

  it('does not silently select the first of multiple projects', async () => {
    const context = TestBed.inject(ProjectContextService);

    await firstValueFrom(context.ensureLoaded());

    expect(context.projects()).toEqual(projects);
    expect(context.selectedProjectId()).toBe('');
    expect(context.hasProject()).toBeFalsy();
  });

  it('persists selection per tenant and user and synchronizes the query', async () => {
    const context = TestBed.inject(ProjectContextService);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await firstValueFrom(context.ensureLoaded());

    expect(context.selectProject('project-b')).toBeTruthy();
    await vi.waitFor(() => expect(router.url).toContain('projectId=project-b'));

    expect(context.selectedProjectId()).toBe('project-b');
    expect(localStorage.getItem('ananta.project.selected.tenant-a%3Auser-a')).toBe('project-b');
  });

  it('creates, selects and canonicalizes a server-owned project id', async () => {
    const context = TestBed.inject(ProjectContextService);
    await firstValueFrom(context.ensureLoaded());

    const created = await firstValueFrom(context.createProject({
      name: ' Gamma ',
      description: ' New project ',
    }));

    expect(createProject).toHaveBeenCalledWith({ name: 'Gamma', description: 'New project' });
    expect(created.id).toBe('project-c');
    expect(context.selectedProjectId()).toBe('project-c');
  });
});

function project(id: string, name: string): ProjectSummary {
  return {
    id,
    name,
    description: null,
    status: 'active',
    isActive: true,
    origin: 'native',
    teamId: null,
    version: 1,
    createdAt: 1,
    updatedAt: 1,
    archivedAt: null,
  };
}
