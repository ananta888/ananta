import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';

import { ProjectContextService } from '../services/project-context.service';
import { ProjectContextSwitcherComponent } from './project-context-switcher.component';

describe('ProjectContextSwitcherComponent', () => {
  it('shows projects, archive status and delegates selection', async () => {
    const selectProject = vi.fn();
    const context = {
      projects: signal([
        { id: 'active', name: 'Active', status: 'active' },
        { id: 'archived', name: 'Archived', status: 'archived' },
      ]),
      selectedProjectId: signal('active'),
      loading: signal(false),
      error: signal(''),
      ensureLoaded: vi.fn(() => of({})),
      selectProject,
    };
    await TestBed.configureTestingModule({
      imports: [ProjectContextSwitcherComponent],
      providers: [
        provideRouter([]),
        { provide: ProjectContextService, useValue: context },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProjectContextSwitcherComponent);
    fixture.detectChanges();

    const select = fixture.nativeElement.querySelector('select') as HTMLSelectElement;
    select.value = 'active';
    select.dispatchEvent(new Event('change'));

    expect(selectProject).toHaveBeenCalledWith('active');
    expect(fixture.nativeElement.textContent).toContain('Archived (archiviert)');
    expect(fixture.nativeElement.textContent).toContain('Neues Projekt');
  });
});
