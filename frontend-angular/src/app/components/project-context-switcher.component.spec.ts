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
      selectionBlocked: signal(false),
      selectionBlockMessage: signal(''),
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

  it('disables project selection while a context owner holds a lock', async () => {
    const selectionBlocked = signal(true);
    const selectionBlockMessage = signal('Die Organisation wird gerade instanziiert.');
    const context = {
      projects: signal([
        { id: 'active', name: 'Active', status: 'active' },
        { id: 'other', name: 'Other', status: 'active' },
      ]),
      selectedProjectId: signal('active'),
      loading: signal(false),
      selectionBlocked,
      selectionBlockMessage,
      error: signal(''),
      ensureLoaded: vi.fn(() => of({})),
      selectProject: vi.fn(),
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
    await fixture.whenStable();
    fixture.detectChanges();

    const select = fixture.nativeElement.querySelector('select') as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(select.title).toBe('Die Organisation wird gerade instanziiert.');

    selectionBlocked.set(false);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(select.disabled).toBe(false);
    expect(select.hasAttribute('title')).toBe(false);
  });
});
