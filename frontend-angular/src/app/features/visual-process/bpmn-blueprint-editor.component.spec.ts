import { ChangeDetectorRef } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, Subject } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type BpmnModeler from 'bpmn-js/lib/Modeler';
import { BpmnBlueprintEditorComponent } from './bpmn-blueprint-editor.component';
import { BpmnImportResult, VisualProcessApiService, VpGraph, WorkflowPreflight, WorkflowRequestResult, WorkflowStatus } from './visual-process-api.service';

const graph: VpGraph = { id: 'synthetic', name: 'Test', description: '', version: '1', steps: [], edges: [], tags: [] };
const validation = { valid: true, error_count: 0, warning_count: 0, issues: [] };
const imported: BpmnImportResult = {
  graph, warnings: ['Hub conversion warning'], validation,
  execution_support: { schema: 'synthetic', supported: true, runtime_verified: false, issues: [] },
};
const compiled: WorkflowRequestResult = { workflow_request: {}, validation, errors: [] };
const readiness: WorkflowPreflight = { ready: true, workflow_id: graph.id, runtime_id: 'ananta-native',
  plan_hash: 'a'.repeat(64), definition_hash: 'b'.repeat(64), reason_codes: [] };
const status: WorkflowStatus = { schema: 'synthetic', workflow_id: graph.id, backend: 'ananta-native', status: 'running',
  plan_hash: readiness.plan_hash, definition_hash: readiness.definition_hash,
  run_id: 'synthetic-run', revision: 3, checkpoint_ref: 'synthetic-checkpoint', allowed_commands: ['cancel'] };

interface EditorHarness {
  modeler: BpmnModeler;
  invalidateCompilation(): void;
}

describe('BPMN execution admission UI', () => {
  let component: BpmnBlueprintEditorComponent;
  let harness: EditorHarness;
  let compilation: Subject<WorkflowRequestResult>;
  let started: Subject<WorkflowStatus>;
  let api: Record<'importBpmn' | 'compileWorkflowRequest' | 'startWorkflowFromGraph' | 'getWorkflowStatus'
    | 'preflightWorkflowFromGraph' | 'getWorkflowEvents' | 'getBpmnEdgeTrace' | 'controlBpmnWorkflow', ReturnType<typeof vi.fn>>;
  let saveXML: ReturnType<typeof vi.fn>;
  let importXML: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    compilation = new Subject();
    started = new Subject();
    api = {
      importBpmn: vi.fn(() => of(imported)),
      compileWorkflowRequest: vi.fn(() => compilation),
      startWorkflowFromGraph: vi.fn(() => started),
      getWorkflowStatus: vi.fn(() => of(status)),
      preflightWorkflowFromGraph: vi.fn(() => of(readiness)),
      getWorkflowEvents: vi.fn(() => of({ events: [] })),
      getBpmnEdgeTrace: vi.fn(() => of(null)),
      controlBpmnWorkflow: vi.fn(() => of(status)),
    };
    TestBed.configureTestingModule({ providers: [
      { provide: VisualProcessApiService, useValue: api },
      { provide: ChangeDetectorRef, useValue: { markForCheck: vi.fn() } },
    ] });
    component = TestBed.runInInjectionContext(() => new BpmnBlueprintEditorComponent());
    harness = component as unknown as EditorHarness;
    saveXML = vi.fn(async () => ({ xml: '<definitions/>' }));
    importXML = vi.fn(async () => ({ warnings: [] }));
    const services = {
      canvas: { zoom: vi.fn(), addMarker: vi.fn(), removeMarker: vi.fn() },
      selection: { get: () => [] }, elementRegistry: { get: () => undefined },
      commandStack: { canUndo: () => false, canRedo: () => false },
    };
    harness.modeler = { saveXML, importXML, get: (name: keyof typeof services) => services[name], destroy: vi.fn() } as unknown as BpmnModeler;
  });

  afterEach(() => { component.ngOnDestroy(); TestBed.resetTestingModule(); vi.useRealTimers(); });

  async function compile(): Promise<void> {
    component.compileWorkflowRequest();
    await vi.waitFor(() => expect(api.compileWorkflowRequest).toHaveBeenCalled());
    compilation.next(compiled);
  }

  it('does not start before successful compilation or after model edits', async () => {
    component.startWorkflow();
    expect(api.startWorkflowFromGraph).not.toHaveBeenCalled();
    await compile();
    expect(component.canStart()).toBe(true);
    expect(component.executionText()).toContain('Kein Ausführungsnachweis');
    expect(component.warnings()).toContain('Hub conversion warning');
    harness.invalidateCompilation();
    component.startWorkflow();
    expect(api.startWorkflowFromGraph).not.toHaveBeenCalled();
    expect(component.resultText()).toBe('');
  });

  it.each(['success', 'error'])('ignores an old compilation %s after a model revision change', async outcome => {
    component.compileWorkflowRequest();
    await vi.waitFor(() => expect(api.compileWorkflowRequest).toHaveBeenCalled());
    harness.invalidateCompilation();
    if (outcome === 'success') compilation.next(compiled);
    else compilation.error({ error: 'stale failure' });
    expect(component.canStart()).toBe(false);
    expect(component.executionText()).toContain('Definition geändert');
    expect(component.resultText()).toBe('');
  });

  it('ignores an older request even if the diagram revision has not changed', async () => {
    component.compileWorkflowRequest();
    await vi.waitFor(() => expect(api.compileWorkflowRequest).toHaveBeenCalledTimes(1));
    const latest = new Subject<WorkflowRequestResult>();
    api.compileWorkflowRequest.mockReturnValueOnce(latest);
    component.compileWorkflowRequest();
    await vi.waitFor(() => expect(api.compileWorkflowRequest).toHaveBeenCalledTimes(2));
    latest.next(compiled);
    compilation.error({ error: 'stale' });
    expect(component.canStart()).toBe(true);
  });

  it('shows unsupported element identities and blocks compilation and start', async () => {
    api.importBpmn.mockReturnValue(of({ ...imported, execution_support: {
      ...imported.execution_support, supported: false,
      issues: [{ element_id: 'timer_1', reason_code: 'bpmn_child_unsupported', detail: 'timer' }],
    } }));
    component.compileWorkflowRequest();
    await vi.waitFor(() => expect(api.importBpmn).toHaveBeenCalled());
    expect(api.compileWorkflowRequest).not.toHaveBeenCalled();
    expect(component.canStart()).toBe(false);
    expect(component.executionText()).toContain('timer_1: bpmn_child_unsupported');
  });

  it('requires a positive Hub support report', async () => {
    api.importBpmn.mockReturnValue(of({ ...imported, execution_support: undefined }));
    component.compileWorkflowRequest();
    await vi.waitFor(() => expect(api.importBpmn).toHaveBeenCalled());
    expect(api.compileWorkflowRequest).not.toHaveBeenCalled();
    expect(component.canStart()).toBe(false);
  });

  it.each([
    { ...compiled, validation: { ...validation, valid: false } },
    { ...compiled, errors: ['bpmn_unsupported'] },
    { ...compiled, errors: undefined },
  ])('does not promote invalid or incomplete compilation to eligible', async result => {
    component.compileWorkflowRequest();
    await vi.waitFor(() => expect(api.compileWorkflowRequest).toHaveBeenCalled());
    compilation.next(result);
    expect(component.canStart()).toBe(false);
    expect(component.executionText()).toContain('Nicht kompilierbar');
  });

  it('compiles captured XML without overwriting or compiling the editable buffer', async () => {
    let resolve!: (value: { xml: string }) => void;
    saveXML.mockReturnValue(new Promise<{ xml: string }>(done => { resolve = done; }));
    component.compileGraph();
    component.xmlBuffer = 'an unimported draft';
    resolve({ xml: '<snapshot/>' });
    await vi.waitFor(() => expect(api.importBpmn).toHaveBeenCalledWith('<snapshot/>'));
    expect(component.xmlBuffer).toBe('an unimported draft');
  });

  it('does not export into a newer XML draft', async () => {
    let resolve!: (value: { xml: string }) => void;
    saveXML.mockReturnValue(new Promise<{ xml: string }>(done => { resolve = done; }));
    const pending = component.exportXml();
    component.xmlBuffer = 'new draft';
    resolve({ xml: '<old/>' });
    await pending;
    expect(component.xmlBuffer).toBe('new draft');
  });

  it('does not send XML serialized before an edit to the Hub', async () => {
    let resolve!: (value: { xml: string }) => void;
    saveXML.mockReturnValue(new Promise<{ xml: string }>(done => { resolve = done; }));
    component.compileGraph();
    harness.invalidateCompilation();
    resolve({ xml: '<old/>' });
    await Promise.resolve();
    expect(api.importBpmn).not.toHaveBeenCalled();
  });

  it('ignores stale graph import errors', async () => {
    const pending = new Subject<BpmnImportResult>();
    api.importBpmn.mockReturnValueOnce(pending);
    component.compileGraph();
    await vi.waitFor(() => expect(api.importBpmn).toHaveBeenCalled());
    harness.invalidateCompilation();
    pending.error({ error: 'stale import' });
    expect(component.resultText()).toBe('');
  });

  it('keeps lossy-import warnings across compilation and edits until a clean import', async () => {
    importXML.mockResolvedValueOnce({ warnings: [{ message: 'unknown BPMN child' }] });
    await component.importXml();
    await compile();
    expect(component.canStart()).toBe(false);
    expect(component.warnings().join()).toContain('unknown BPMN child');
    harness.invalidateCompilation();
    expect(component.warnings().join()).toContain('unknown BPMN child');
    await component.importXml();
    expect(component.warnings()).toEqual([]);
  });

  it('serializes imports and recovers after a malformed XML import', async () => {
    let reject!: (error: Error) => void;
    importXML.mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail; }));
    const pending = component.importXml();
    await component.importXml();
    expect(importXML).toHaveBeenCalledTimes(1);
    reject(new Error('malformed XML'));
    await pending;
    expect(component.importing()).toBe(false);
    expect(component.warnings().join()).toContain('fehlgeschlagen');
    await component.loadStarter();
    expect(component.warnings()).toEqual([]);
  });

  it('starts the exact preflight graph only once and reports running as a Hub status', async () => {
    await compile();
    component.xmlBuffer = '<different draft/>';
    component.startWorkflow();
    component.startWorkflow();
    expect(api.startWorkflowFromGraph).toHaveBeenCalledTimes(1);
    expect(api.startWorkflowFromGraph.mock.calls[0][0]).toBe(graph);
    expect(api.startWorkflowFromGraph.mock.calls[0][1]).toEqual({
      policy_scope: { source: 'bpmn_blueprint_editor' }, expected_plan_hash: readiness.plan_hash,
      expected_definition_hash: readiness.definition_hash,
    });
    started.next(status);
    expect(component.runtimeView()?.status).toBe('running');
    expect(component.runtimeMessage()).toContain('kein Ausführungs- oder Release-Nachweis');
    expect(component.canStart()).toBe(false);
  });

  it('shows Hub start rejection without claiming execution', async () => {
    await compile();
    component.startWorkflow();
    started.error({ error: { data: { reason_code: 'workflow_runtime_capability_unavailable' } } });
    expect(component.resultText()).toContain('workflow_runtime_capability_unavailable');
    expect(component.runtimeView()).toBeNull();
    expect(component.starting()).toBe(false);
    expect(component.canStart()).toBe(false);
  });

  it('ignores status responses for a previously selected workflow', () => {
    const pending = new Subject<WorkflowStatus>();
    api.getWorkflowStatus.mockReturnValueOnce(pending);
    component.workflowId = 'workflow-a';
    component.refreshRuntime();
    component.workflowId = 'workflow-b';
    component.clearRuntime();
    pending.next(status);
    expect(component.runtimeView()).toBeNull();
  });

  it('rejects mismatched workflow identities and clears obsolete runtime status on lookup failure', () => {
    component.workflowId = 'workflow-b';
    component.refreshRuntime();
    expect(component.runtimeView()).toBeNull();
    expect(component.runtimeMessage()).toContain('anderen Workflow');
    component.workflowId = status.workflow_id;
    component.refreshRuntime();
    expect(component.runtimeView()?.status).toBe('running');
    const pending = new Subject<WorkflowStatus>();
    api.getWorkflowStatus.mockReturnValueOnce(pending);
    component.refreshRuntime();
    pending.error(new Error('unauthorized'));
    expect(component.runtimeView()).toBeNull();
  });

  it('does not accept late compilation after destruction', async () => {
    component.compileWorkflowRequest();
    await vi.waitFor(() => expect(api.compileWorkflowRequest).toHaveBeenCalled());
    component.ngOnDestroy();
    compilation.next(compiled);
    expect(component.canStart()).toBe(false);
  });

  it('keeps Start disabled until current tenant/workflow preflight finishes', async () => {
    const pending = new Subject<WorkflowPreflight>();
    api.preflightWorkflowFromGraph.mockReturnValue(pending);
    await compile();
    expect(component.canStart()).toBe(false);
    expect(api.preflightWorkflowFromGraph).toHaveBeenCalledWith(graph, { policy_scope: { source: 'bpmn_blueprint_editor' } });
    pending.next(readiness);
    expect(component.canStart()).toBe(true);
  });

  it.each([
    { ...readiness, ready: false, reason_codes: ['runtime_disabled'] },
    { ...readiness, workflow_id: 'other' }, { ...readiness, plan_hash: '' },
    { ...readiness, plan_hash: 'not-a-hash' }, { ...readiness, definition_hash: 'B'.repeat(64) },
    { ...readiness, definition_hash: '' }, { ...readiness, reason_codes: undefined }, { ...readiness, runtime_id: null },
    null,
  ])('fails closed for negative, incomplete or mismatched readiness', async result => {
    api.preflightWorkflowFromGraph.mockReturnValue(of(result));
    await compile();
    expect(component.canStart()).toBe(false);
    component.startWorkflow();
    expect(api.startWorkflowFromGraph).not.toHaveBeenCalled();
  });

  it.each(['success', 'error'])('ignores a late preflight %s after edits', async outcome => {
    const pending = new Subject<WorkflowPreflight>();
    api.preflightWorkflowFromGraph.mockReturnValue(pending);
    await compile();
    harness.invalidateCompilation();
    if (outcome === 'success') pending.next(readiness);
    else pending.error(new Error('stale'));
    expect(component.canStart()).toBe(false);
    expect(component.editorDefinitionHash()).toBe('');
    expect(component.executionText()).toContain('Definition geändert');
  });

  it('does not accept a Start response for another definition', async () => {
    await compile();
    component.startWorkflow();
    started.next({ ...status, definition_hash: 'changed' });
    expect(component.runtimeView()).toBeNull();
    expect(component.runtimeMessage()).toContain('passt nicht');
  });

  it('reconstructs history on lookup, without cached status or variable payloads', () => {
    api.getWorkflowEvents.mockReturnValue(of({ events: [{ schema: 'ananta.workflow_event.v1',
      workflow_id: status.workflow_id, run_id: status['run_id'], event_id: 'synthetic-event', sequence: 1,
      event_type: 'workflow.step.completed', payload: { selected_edge: 'Flow_2', variables: { secret: 'HIDDEN' } } }] }));
    component.workflowId = status.workflow_id;
    component.refreshRuntime();
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);
    expect(api.getBpmnEdgeTrace).toHaveBeenCalledWith(status.workflow_id, status['run_id']);
    expect(component.runtimeView()?.edges[0].id).toBe('Flow_2');
    expect(JSON.stringify(component.runtimeView())).not.toContain('HIDDEN');
  });

  it('discards history if the Hub revision changes while reloading', () => {
    api.getWorkflowStatus.mockReturnValueOnce(of(status)).mockReturnValueOnce(of({ ...status, revision: 4 }));
    component.workflowId = status.workflow_id;
    component.refreshRuntime();
    expect(component.runtimeView()).toBeNull();
    expect(component.runtimeMessage()).toContain('Revision');
  });

  it.each(['resume', 'retry', 'cancel'] as const)('sends %s only with explicit Hub support and loaded binding', command => {
    api.getWorkflowStatus.mockReturnValue(of({ ...status, allowed_commands: [command] }));
    component.workflowId = status.workflow_id;
    component.refreshRuntime();
    component.controlWorkflow(command);
    expect(api.controlBpmnWorkflow).toHaveBeenCalledWith(status.workflow_id, command, {
      expected_revision: 3, run_id: status['run_id'], plan_hash: status['plan_hash'], checkpoint_ref: status['checkpoint_ref'],
    }, expect.stringMatching(/^bpmn-command-/));
  });

  it('does not infer supported controls from a running status', () => {
    api.getWorkflowStatus.mockReturnValue(of({ ...status, allowed_commands: undefined }));
    component.workflowId = status.workflow_id;
    component.refreshRuntime();
    component.controlWorkflow('cancel');
    expect(api.controlBpmnWorkflow).not.toHaveBeenCalled();
  });

  it('blocks duplicate controls and clears a stale revision after rejection', () => {
    const pending = new Subject<WorkflowStatus>();
    api.controlBpmnWorkflow.mockReturnValue(pending);
    component.workflowId = status.workflow_id;
    component.refreshRuntime();
    component.controlWorkflow('cancel');
    component.controlWorkflow('cancel');
    expect(api.controlBpmnWorkflow).toHaveBeenCalledTimes(1);
    pending.error({ status: 409, error: { reason_code: 'stale_workflow_revision' } });
    expect(component.runtimeView()).toBeNull();
    expect(component.canControl('cancel')).toBe(false);
  });

  it('ignores a pending command when another workflow is selected', () => {
    const pending = new Subject<WorkflowStatus>();
    api.controlBpmnWorkflow.mockReturnValue(pending);
    component.workflowId = status.workflow_id;
    component.refreshRuntime();
    component.controlWorkflow('cancel');
    component.workflowId = 'other';
    component.clearRuntime();
    pending.next(status);
    expect(component.runtimeView()).toBeNull();
    expect(component.controlling()).toBe(false);
  });

  it('bounds an unanswered control and requires a new status read', async () => {
    vi.useFakeTimers();
    api.controlBpmnWorkflow.mockReturnValue(new Subject<WorkflowStatus>());
    component.workflowId = status.workflow_id;
    component.refreshRuntime();
    component.controlWorkflow('cancel');
    await vi.advanceTimersByTimeAsync(15001);
    expect(component.controlling()).toBe(false);
    expect(component.runtimeView()).toBeNull();
    expect(component.runtimeMessage()).toContain('Status erneut laden');
  });

  it('fails closed if a reload loses authorization during the second status read', () => {
    const denied = new Subject<WorkflowStatus>();
    api.getWorkflowStatus.mockReturnValueOnce(of(status)).mockReturnValueOnce(denied);
    component.workflowId = status.workflow_id;
    component.refreshRuntime();
    denied.error({ status: 403, error: { variables: { secret: 'HIDDEN' } } });
    expect(component.runtimeView()).toBeNull();
    expect(component.runtimeMessage()).toContain('nicht geladen');
    expect(component.runtimeMessage()).not.toContain('HIDDEN');
  });
});
