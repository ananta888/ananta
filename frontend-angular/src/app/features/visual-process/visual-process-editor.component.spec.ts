import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { vi, describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { of, throwError } from 'rxjs';

import { VisualProcessEditorComponent } from './visual-process-editor.component';
import { VisualProcessApiService, VpGraph, VpStep } from './visual-process-api.service';
import { VpCanvasInteractionService } from './vp-canvas-interaction.service';
import { VpImportExportService } from './vp-import-export.service';
import { VpWorkflowRunnerService } from './vp-workflow-runner.service';

beforeAll(async () => {
  await ɵresolveComponentResources(resource =>
    readFile(new URL(resource, import.meta.url), 'utf8'),
  );
});

function emptyGraph(): VpGraph {
  return { id: '', name: '', description: '', version: '1.0.0', steps: [], edges: [], tags: [] };
}

function step(id: string, kind = 'patch_propose'): VpStep {
  return {
    id,
    kind,
    label: `Step ${id}`,
    role: '',
    enabled: true,
    io: { inputs: [], outputs: [] },
    position: { x: 0, y: 0 },
    policy_hints: [],
    gate: false,
  } as VpStep;
}

describe('VisualProcessEditorComponent (FSR-T015 acceptance)', () => {
  let api: {
    listPresets: ReturnType<typeof vi.fn>;
    listSkillProfiles: ReturnType<typeof vi.fn>;
    listTaskKinds: ReturnType<typeof vi.fn>;
    listSavedGraphs: ReturnType<typeof vi.fn>;
    validate: ReturnType<typeof vi.fn>;
    dryRun: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    api = {
      listPresets: vi.fn().mockReturnValue(of([])),
      listSkillProfiles: vi.fn().mockReturnValue(of([])),
      listTaskKinds: vi.fn().mockReturnValue(of([])),
      listSavedGraphs: vi.fn().mockReturnValue(of([])),
      validate: vi.fn().mockReturnValue(of({ valid: true, error_count: 0, warning_count: 0, issues: [] })),
      dryRun: vi.fn().mockReturnValue(of({} as any)),
    };

    await TestBed.configureTestingModule({
      imports: [VisualProcessEditorComponent],
      providers: [
        { provide: VisualProcessApiService, useValue: api },
        VpWorkflowRunnerService,
        VpCanvasInteractionService,
        VpImportExportService,
      ],
    }).compileComponents();
  });

  it('mounts and initializes with an empty graph signal', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const graph = fixture.componentInstance.graph();
    expect(graph).toBeDefined();
    expect(Array.isArray(graph.steps)).toBe(true);
    expect(Array.isArray(graph.edges)).toBe(true);
  });

  it('loads presets, skill profiles, task kinds and saved graphs on init', () => {
    TestBed.createComponent(VisualProcessEditorComponent).detectChanges();
    expect(api.listPresets).toHaveBeenCalledTimes(1);
    expect(api.listSkillProfiles).toHaveBeenCalledTimes(1);
    expect(api.listTaskKinds).toHaveBeenCalledTimes(1);
    expect(api.listSavedGraphs).toHaveBeenCalledTimes(1);
  });

  it('adds a new step via addStep and updates the graph signal', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const before = component.graph().steps.length;
    component.addStep();
    const after = component.graph().steps.length;
    expect(after).toBe(before + 1);
    expect(component.graph().steps[after - 1].kind).toBeTruthy();
  });

it('routes validation calls through VpWorkflowRunnerService, not directly to api', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance as unknown as {
      validateGraph: () => void;
      workflowRunner: { validate: (...args: unknown[]) => void };
    };
    const validateSpy = vi.spyOn(component.workflowRunner, 'validate').mockImplementation(() => {});
    component.validateGraph();
    expect(validateSpy).toHaveBeenCalledTimes(1);
    expect(api.validate).not.toHaveBeenCalled();
  });

  it('routes dry-run calls through VpWorkflowRunnerService', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance as unknown as {
      runDryRun: () => void;
      workflowRunner: { dryRun: (...args: unknown[]) => void };
    };
    const dryRunSpy = vi.spyOn(component.workflowRunner, 'dryRun').mockImplementation(() => {});
    component.runDryRun();
    expect(dryRunSpy).toHaveBeenCalledTimes(1);
    expect(api.dryRun).not.toHaveBeenCalled();
  });

  it('handles listSavedGraphs failure without breaking init', () => {
    api.listSavedGraphs.mockReturnValueOnce(throwError(() => new Error('boom')));
    expect(() => TestBed.createComponent(VisualProcessEditorComponent).detectChanges()).not.toThrow();
  });

  it('renders the svg canvas element after init', () => {
    const fixture = TestBed.createComponent(VisualProcessEditorComponent);
    fixture.detectChanges();
    const html = fixture.nativeElement as HTMLElement;
    const svg = html.querySelector('svg');
    expect(svg).not.toBeNull();
  });
});