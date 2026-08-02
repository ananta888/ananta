import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';

import { ProjectContextService } from '../../services/project-context.service';
import { ProjectManagementComponent } from './project-management.component';

describe('ProjectManagementComponent', () => {
  let fixture: ComponentFixture<ProjectManagementComponent>;
  const selectedProjectId = signal('project-alpha');
  const context = {
    projects: signal([{
      id: 'project-alpha',
      name: 'Alpha',
      description: 'Primary project',
      status: 'active',
    }]),
    selectedProjectId,
    loading: signal(false),
    error: signal(''),
    ensureLoaded: vi.fn(() => of({})),
    createProject: vi.fn(),
    selectProject: vi.fn(() => true),
    urlWithProject: vi.fn(() => '/sources/journey?projectId=project-alpha'),
  };
  const router = { navigateByUrl: vi.fn(() => Promise.resolve(true)) };

  beforeEach(async () => {
    vi.clearAllMocks();
    selectedProjectId.set('project-alpha');
    await TestBed.configureTestingModule({
      imports: [ProjectManagementComponent],
      providers: [
        { provide: ProjectContextService, useValue: context },
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: { get: () => null } } } },
        { provide: Router, useValue: router },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(ProjectManagementComponent);
    fixture.detectChanges();
  });

  it('shows a prominent project-bound source CTA and preserves the selected project in the URL', () => {
    const buttons = Array.from(
      fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>,
    );
    const sourceCta = buttons.find(button => button.textContent?.includes('Git oder Ordner hinzufügen'));

    expect(sourceCta).toBeDefined();
    sourceCta?.click();

    expect(context.urlWithProject).toHaveBeenCalledWith(
      '/sources/journey',
      'project-alpha',
    );
    expect(router.navigateByUrl).toHaveBeenCalledWith(
      '/sources/journey?projectId=project-alpha',
    );
  });
});
