import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { of, throwError } from 'rxjs';

import { ControlPlaneFacade } from '../features/control-plane/control-plane.facade';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { PersonalWorkspaceComponent } from './personal-workspace.component';

describe('personal workspace start actions', () => {
  afterEach(() => TestBed.resetTestingModule());

  function setup(params: {
    agents?: Array<{ name: string; role?: 'hub' | 'worker'; url: string }>;
    listGoals?: unknown;
  } = {}) {
    const directory = {
      list: vi.fn(() => params.agents ?? [{ name: 'hub', role: 'hub', url: 'http://hub:5000' }]),
    };
    const hubApi = {
      listGoals: vi.fn(() => params.listGoals ?? of([
        { id: 'goal-open', summary: 'Open', status: 'open' },
        { id: 'goal-active', summary: 'Active', status: 'in_progress' },
        { id: 'goal-done', summary: 'Done', status: 'completed' },
        { id: 'goal-failed', summary: 'Failed', status: 'failed' },
      ])),
    };
    const router = {
      navigate: vi.fn(),
    };
    TestBed.configureTestingModule({
      providers: [
        { provide: AgentDirectoryService, useValue: directory },
        { provide: ControlPlaneFacade, useValue: hubApi },
        { provide: Router, useValue: router },
      ],
    });
    return {
      component: TestBed.runInInjectionContext(() => new PersonalWorkspaceComponent()),
      directory,
      hubApi,
      router,
    };
  }

  it('loads goals from the configured hub and counts only open work', () => {
    const { component, hubApi } = setup();

    component.loadGoals();

    expect(hubApi.listGoals).toHaveBeenCalledWith('http://hub:5000');
    expect(component.loading).toBe(false);
    expect(component.error).toBe('');
    expect(component.goals.map(goal => goal.id)).toEqual(['goal-open', 'goal-active', 'goal-done', 'goal-failed']);
    expect(component.openGoalCount()).toBe(2);
  });

  it('surfaces missing hub and hub loading errors without navigation side effects', () => {
    const missingHub = setup({ agents: [{ name: 'alpha', role: 'worker', url: 'http://worker:5001' }] });
    missingHub.component.loadGoals();

    expect(missingHub.component.error).toContain('Kein Hub konfiguriert');
    expect(missingHub.hubApi.listGoals).not.toHaveBeenCalled();

    TestBed.resetTestingModule();
    const offline = setup({
      listGoals: throwError(() => ({ error: { message: 'Hub offline' } })),
    });
    offline.component.loadGoals();

    expect(offline.component.loading).toBe(false);
    expect(offline.component.error).toBe('Hub offline');
    expect(offline.router.navigate).not.toHaveBeenCalled();
  });

  it('routes the main start actions to dashboard, templates and goal detail', () => {
    const { component, router } = setup();

    component.goPlan();
    component.goTemplates();
    component.openGoal('goal-123');

    expect(router.navigate).toHaveBeenCalledWith(['/dashboard'], { fragment: 'quick-goal' });
    expect(router.navigate).toHaveBeenCalledWith(['/templates']);
    expect(router.navigate).toHaveBeenCalledWith(['/goal', 'goal-123']);
  });

  it('keeps user-facing status labels stable', () => {
    const { component } = setup();

    expect(component.friendlyStatus('completed')).toBe('abgeschlossen');
    expect(component.friendlyStatus('failed')).toBe('fehlgeschlagen');
    expect(component.friendlyStatus('in_progress')).toBe('in Arbeit');
    expect(component.friendlyStatus(undefined)).toBe('offen');
  });
});
