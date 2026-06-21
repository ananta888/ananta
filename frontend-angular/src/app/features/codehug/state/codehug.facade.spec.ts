import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { of, throwError, firstValueFrom } from 'rxjs';

import { CodeHugFacade } from './codehug.facade';
import { CodeCompassService } from '../services/code-compass.service';
import { AgentRunService } from '../services/agent-run.service';
import { PolicyService } from '../services/policy.service';
import { ContextPackageService } from '../services/context-package.service';

function mockCC() {
  return {
    listProjects: vi.fn(() => of([
      { id: 'p1', name: 'P1', rootPath: '/p1', indexStatus: 'complete' },
      { id: 'p2', name: 'P2', rootPath: '/p2', indexStatus: 'partial' },
    ])),
    getProject: vi.fn((id: string) => of({ id, name: 'P-' + id, rootPath: '/' + id, indexStatus: 'complete' })),
    triggerReindex: vi.fn(() => of({ jobId: 'job-1' })),
  };
}
function mockRuns() {
  return { listRuns: vi.fn(() => of([])) };
}
function mockPolicy() {
  return { loadCurrentSnapshot: vi.fn(() => of({ sensitiveFilePatterns: [], id: 'snap-1' })) };
}
function mockPackages() {
  return { setSensitivePatterns: vi.fn() };
}

describe('CodeHugFacade', () => {
  let facade: CodeHugFacade;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        CodeHugFacade,
        { provide: CodeCompassService, useValue: mockCC() },
        { provide: AgentRunService, useValue: mockRuns() },
        { provide: PolicyService, useValue: mockPolicy() },
        { provide: ContextPackageService, useValue: mockPackages() },
      ],
    });
    facade = TestBed.inject(CodeHugFacade);
  });

  it('starts with no project', () => {
    expect(facade.currentProjectId()).toBeNull();
    expect(facade.hasProject()).toBe(false);
  });

  it('loadProjects populates list', () => {
    facade.loadProjects();
    expect(facade.projects().length).toBe(2);
    expect(facade.projects()[0].id).toBe('p1');
  });

  it('selectProject sets id and loads metadata', () => {
    facade.selectProject('p1');
    expect(facade.currentProjectId()).toBe('p1');
    expect(facade.hasProject()).toBe(true);
    expect(facade.currentProject()?.name).toBe('P-p1');
  });

  it('selectProject on error sets error and keeps loading=false', () => {
    TestBed.resetTestingModule();
    const cc = { ...mockCC(), getProject: vi.fn(() => throwError(() => new Error('boom'))) };
    TestBed.configureTestingModule({
      providers: [
        CodeHugFacade,
        { provide: CodeCompassService, useValue: cc },
        { provide: AgentRunService, useValue: mockRuns() },
        { provide: PolicyService, useValue: mockPolicy() },
        { provide: ContextPackageService, useValue: mockPackages() },
      ],
    });
    const f = TestBed.inject(CodeHugFacade);
    f.selectProject('p1');
    expect(f.projectError()).toContain('boom');
    expect(f.loadingProject()).toBe(false);
  });

  it('clearProject resets state', () => {
    facade.selectProject('p1');
    facade.clearProject();
    expect(facade.currentProjectId()).toBeNull();
    expect(facade.hasProject()).toBe(false);
    expect(facade.projectError()).toBeNull();
  });

  it('triggerReindex requires current project', () => {
    facade.triggerReindex(); // no-op
    expect(facade.currentProjectId()).toBeNull();
  });

  it('triggerReindex calls cc.triggerReindex when project is set', () => {
    const cc = TestBed.inject(CodeCompassService) as any;
    facade.selectProject('p1');
    facade.triggerReindex();
    expect(cc.triggerReindex).toHaveBeenCalledWith('p1');
  });
});