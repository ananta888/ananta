import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRouteSnapshot, Router, RouterStateSnapshot, UrlTree } from '@angular/router';
import { Observable, firstValueFrom, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { ProjectContextService } from '../services/project-context.service';
import { projectContextGuard } from './project-context.guard';

describe('projectContextGuard', () => {
  const execute = () => TestBed.runInInjectionContext(() => projectContextGuard(
    {} as ActivatedRouteSnapshot,
    { url: '/dashboard?view=active' } as RouterStateSnapshot,
  )) as Observable<boolean | UrlTree>;

  it('allows a project-scoped route after the selected project is loaded', async () => {
    const ensureLoaded = vi.fn(() => of(undefined));
    TestBed.configureTestingModule({
      providers: [
        {
          provide: ProjectContextService,
          useValue: {
            ensureLoaded,
            selectedProjectId: signal('project-1'),
            hasProject: signal(true),
          },
        },
        { provide: Router, useValue: { createUrlTree: vi.fn() } },
      ],
    });

    await expect(firstValueFrom(execute())).resolves.toBe(true);
    expect(ensureLoaded).toHaveBeenCalledOnce();
  });

  it('redirects to project management and keeps the requested return URL', async () => {
    const redirect = {} as UrlTree;
    const createUrlTree = vi.fn().mockReturnValue(redirect);
    TestBed.configureTestingModule({
      providers: [
        {
          provide: ProjectContextService,
          useValue: {
            ensureLoaded: vi.fn(() => of(undefined)),
            selectedProjectId: signal<string | null>(null),
            hasProject: signal(false),
          },
        },
        { provide: Router, useValue: { createUrlTree } },
      ],
    });

    await expect(firstValueFrom(execute())).resolves.toBe(redirect);
    expect(createUrlTree).toHaveBeenCalledWith(['/projects'], {
      queryParams: { returnUrl: '/dashboard?view=active' },
    });
  });
});
