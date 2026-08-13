import { HttpErrorResponse } from '@angular/common/http';
import { ɵresolveComponentResources } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { Observable, Subject, throwError } from 'rxjs';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  VpEdge,
  VpGraph,
  VpRuntimeOverlay,
  VpStep,
} from '../../visual-process/visual-process-api.service';
import type {
  CaseFlowEdgeTraceProjection,
  CaseFlowEdgeTraceReadModel,
  CaseFlowEdgeTraceTelemetryEntry,
} from './caseflow-edge-trace.models';
import {
  CASEFLOW_AGENT_NODE_TRACE_READER,
  CaseFlowAgentNodeRuntimeInspectorComponent,
} from './caseflow-agent-node-runtime-inspector.component';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('CaseFlowAgentNodeRuntimeInspectorComponent', () => {
  const responses: Subject<CaseFlowEdgeTraceReadModel>[] = [];
  let reader: { read: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    responses.length = 0;
    reader = {
      read: vi.fn((): Observable<CaseFlowEdgeTraceReadModel> => {
        const response = new Subject<CaseFlowEdgeTraceReadModel>();
        responses.push(response);
        return response.asObservable();
      }),
    };
    await TestBed.configureTestingModule({
      imports: [CaseFlowAgentNodeRuntimeInspectorComponent],
      providers: [{ provide: CASEFLOW_AGENT_NODE_TRACE_READER, useValue: reader }],
    }).compileComponents();
  });

  it('filters communication separately by parent, child, loop and exact canonical direction', () => {
    const fixture = createFixture();
    responses[0].next(traceReadModel([
      traceEdge('selected-peer', 'selected', 'peer', 'child-only', telemetry('selected', 20)),
      traceEdge('selected-loop', 'selected', 'selected', 'loop-only', telemetry('selected', 30)),
      traceEdge('peer-selected', 'peer', 'selected', 'parent-only', telemetry('selected', 10)),
    ]));
    fixture.detectChanges();
    clickButton(fixture, 'Kommunikation');

    expect(relationButtons(fixture, 'parent').map(buttonIdentity)).toEqual([
      ['peer-selected', 'peer->selected'],
    ]);
    expect(relationButtons(fixture, 'child').map(buttonIdentity)).toEqual([
      ['selected-peer', 'selected->peer'],
    ]);
    expect(relationButtons(fixture, 'loop').map(buttonIdentity)).toEqual([
      ['selected-loop', 'selected->selected'],
    ]);
    expect(fixture.nativeElement.textContent).toContain('parent-only');
    expect(fixture.nativeElement.textContent).not.toContain('child-only');

    relationButtons(fixture, 'child')[0].click();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('child-only');
    expect(fixture.nativeElement.textContent).not.toContain('parent-only');
    expect(fixture.nativeElement.querySelector('.communication h3').textContent)
      .toContain('selected → peer');
  });

  it('shows allowlisted runtime, current activity and last Hub-allowed error with existing refs', () => {
    const fixture = createFixture();
    const current = telemetry('selected', 40, {
      status: 'running',
      event_ref: 'event-current',
      trace_ref: 'trace-current',
    });
    const failed = telemetry('selected', 50, {
      status: 'failed',
      event_ref: 'event-error',
      trace_ref: 'trace-error',
      error: 'Hub-Fehler',
    });
    responses[0].next(traceReadModel([
      traceEdge('peer-selected', 'peer', 'selected', 'current', current),
      traceEdge('selected-peer', 'selected', 'peer', 'failed', failed),
    ]));
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('model-profile-a');
    expect(text).toContain('provider-a');
    expect(text).toContain('trace-error');
    expect(text).toContain('event-error');
    expect(text).toContain('Hub-Fehler');
    expect(text).toContain('50');
    expect(text).not.toContain('raw-runtime-error');
    expect(text).not.toContain('runtime-secret');
  });

  it('preserves Hub relation order in the full agent trace and labels missing/redacted values', () => {
    const fixture = createFixture();
    responses[0].next(traceReadModel([
      traceEdge(
        'selected-loop',
        'selected',
        'selected',
        '***REDACTED_SECRET***',
        telemetry('selected', 30, { trace_ref: null, error: '***REDACTED_SECRET***' }),
      ),
      traceEdge('peer-selected', 'peer', 'selected', 'parent', telemetry('selected', 10)),
      traceEdge('selected-peer', 'selected', 'peer', 'child', telemetry('selected', 20)),
    ]));
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Redigiert');
    clickButton(fixture, 'Trace');
    const traceGroups = Array.from(
      fixture.nativeElement.querySelectorAll('[data-trace-edge-id]'),
    ) as HTMLElement[];
    expect(traceGroups.map(group => group.dataset['traceEdgeId'])).toEqual([
      'selected-loop', 'peer-selected', 'selected-peer',
    ]);
    expect(traceGroups[0].textContent).toContain('Nicht verfügbar');
    expect(traceGroups[0].textContent).toContain('redigiert');
  });

  it.each([401, 403, 404])(
    'clears loaded runtime and trace synchronously and closes after HTTP %s',
    status => {
      const fixture = createFixture();
      responses[0].next(traceReadModel([
        traceEdge('peer-selected', 'peer', 'selected', 'loaded-message', telemetry('selected', 10)),
      ]));
      fixture.detectChanges();
      expect(fixture.componentInstance.projection).not.toBeNull();

      const revoked: string[] = [];
      fixture.componentInstance.accessRevoked.subscribe(code => revoked.push(code));
      reader.read.mockImplementationOnce(() => throwError(
        () => new HttpErrorResponse({ status }),
      ));
      fixture.componentInstance.reload();

      expect(fixture.componentInstance.projection).toBeNull();
      expect(fixture.componentInstance.selectedRelationKey).toBeNull();
      expect(fixture.componentInstance.closed).toBe(true);
      expect(fixture.componentInstance.loading).toBe(false);
      expect(revoked).toHaveLength(1);
      fixture.detectChanges();
      expect(fixture.nativeElement.textContent).not.toContain('loaded-message');
      expect(fixture.nativeElement.textContent).not.toContain('model-profile-a');
      expect(fixture.nativeElement.textContent).toContain('Zugriff wurde entzogen');
    },
  );

  it('fences a stale async response after the selected run changes', () => {
    const fixture = createFixture();
    fixture.componentRef.setInput('runId', 'run-b');
    fixture.componentRef.setInput('runtimeOverlay', runtimeOverlay('run-b'));
    fixture.detectChanges();

    responses[0].next(traceReadModel([
      traceEdge('peer-selected', 'peer', 'selected', 'stale-run-a', telemetry('selected', 10)),
    ], 'run-a'));
    expect(fixture.componentInstance.projection).toBeNull();

    responses[1].next(traceReadModel([
      traceEdge('peer-selected', 'peer', 'selected', 'fresh-run-b', telemetry('selected', 20)),
    ], 'run-b'));
    fixture.detectChanges();
    clickButton(fixture, 'Kommunikation');

    expect(fixture.nativeElement.textContent).toContain('fresh-run-b');
    expect(fixture.nativeElement.textContent).not.toContain('stale-run-a');
    expect(reader.read).toHaveBeenLastCalledWith({ workflow_id: 'graph-a', run_id: 'run-b' });
  });

  it('is read-only and exposes neither persistence nor a Full Designer escape', () => {
    const write = vi.spyOn(Storage.prototype, 'setItem');
    const fixture = createFixture();
    responses[0].next(traceReadModel([]));
    fixture.detectChanges();

    expect(write).not.toHaveBeenCalled();
    expect(reader).not.toHaveProperty('save');
    expect(fixture.nativeElement.querySelector('form')).toBeNull();
    expect(fixture.nativeElement.querySelector('a')).toBeNull();
    expect((fixture.nativeElement.textContent as string).toLowerCase())
      .not.toContain('full process designer');
  });
});

function createFixture(): ComponentFixture<CaseFlowAgentNodeRuntimeInspectorComponent> {
  const fixture = TestBed.createComponent(CaseFlowAgentNodeRuntimeInspectorComponent);
  fixture.componentRef.setInput('graph', agentGraph());
  fixture.componentRef.setInput('selectedStepId', 'selected');
  fixture.componentRef.setInput('workflowId', 'graph-a');
  fixture.componentRef.setInput('runId', 'run-a');
  fixture.componentRef.setInput('runtimeOverlay', runtimeOverlay('run-a'));
  fixture.detectChanges();
  return fixture;
}

function clickButton(
  fixture: ComponentFixture<CaseFlowAgentNodeRuntimeInspectorComponent>,
  label: string,
): void {
  const button = Array.from(fixture.nativeElement.querySelectorAll('button'))
    .find((candidate: Element) => candidate.textContent?.trim() === label) as HTMLButtonElement;
  button.click();
  fixture.detectChanges();
}

function relationButtons(
  fixture: ComponentFixture<CaseFlowAgentNodeRuntimeInspectorComponent>,
  kind: string,
): HTMLButtonElement[] {
  return Array.from(
    fixture.nativeElement.querySelectorAll(`[data-relation-kind="${kind}"] button`),
  ) as HTMLButtonElement[];
}

function buttonIdentity(button: HTMLButtonElement): readonly (string | undefined)[] {
  return [button.dataset['edgeId'], button.dataset['direction']];
}

function agentGraph(): VpGraph {
  return {
    id: 'graph-a',
    name: 'Agent graph',
    description: '',
    version: '1',
    tags: [],
    steps: [step('selected'), step('peer')],
    edges: [
      edge('selected-peer', 'selected', 'peer'),
      edge('peer-selected', 'peer', 'selected'),
      edge('selected-loop', 'selected', 'selected', 'back_edge'),
    ],
  };
}

function step(id: string): VpStep {
  return {
    id,
    label: id === 'selected' ? 'Selected agent' : 'Peer agent',
    role: 'developer',
    kind: 'coding',
    io: { inputs: [], outputs: [] },
    position: { x: 0, y: 0 },
    policy_hints: [],
    gate: false,
  };
}

function edge(id: string, source: string, target: string, kind = 'always'): VpEdge {
  return { id, source, target, condition: { kind } };
}

function runtimeOverlay(runId: string): VpRuntimeOverlay {
  return {
    run_id: runId,
    workflow_id: 'graph-a',
    process_id: 'graph-a',
    overall_status: 'running',
    current_step_ids: ['selected'],
    steps: {
      selected: {
        step_id: 'selected',
        status: 'running',
        started_at: 5,
        duration_ms: 15,
        selected_model_profile_id: 'model-profile-a',
        selected_provider_id: 'provider-a',
        selected_model: 'model-a',
        error: 'raw-runtime-error',
        gate: { secret: 'runtime-secret' },
      },
      peer: { step_id: 'peer', status: 'pending' },
    },
    updated_at: 60,
  };
}

function traceReadModel(
  edges: readonly CaseFlowEdgeTraceProjection[],
  runId = 'run-a',
): CaseFlowEdgeTraceReadModel {
  return {
    schema: 'ananta.caseflow_edge_trace_read_model.v1',
    workflow_id: 'graph-a',
    run_id: runId,
    catalog_verification_status: 'verified',
    verification_status: edges.every(edge => edge.verification_status === 'verified')
      ? 'verified'
      : 'unverified',
    reason_code: '',
    edges,
    telemetry: {
      source_event_count: edges.length,
      processed_event_count: edges.length,
      rejected_event_count: 0,
      truncated_event_count: 0,
      correlated_edge_count: edges.length,
      redaction_policy: 'user',
      messages_per_edge_limit: 64,
      telemetry_per_edge_limit: 128,
    },
  };
}

function traceEdge(
  edgeId: string,
  sourceStepId: string,
  targetStepId: string,
  message: string,
  entry: CaseFlowEdgeTraceTelemetryEntry,
): CaseFlowEdgeTraceProjection {
  return {
    edge_id: edgeId,
    source_step_id: sourceStepId,
    target_step_id: targetStepId,
    edge_kind: sourceStepId === targetStepId ? 'back_edge' : 'dependency',
    activity_status: 'active',
    verification_status: 'verified',
    reason_code: 'caseflow_edge_correlation_verified_active',
    correlation_basis: 'explicit_edge_id',
    event_refs: entry.event_ref ? [entry.event_ref] : [],
    trace_refs: entry.trace_ref ? [entry.trace_ref] : [],
    messages: [{
      content: message,
      role: 'assistant',
      event_ref: entry.event_ref,
      trace_ref: entry.trace_ref,
      correlation_ref: entry.trace_ref,
      occurred_at: entry.occurred_at,
      verification_status: entry.trace_ref ? 'verified' : 'unverified',
      truncated: false,
    }],
    telemetry: [entry],
    limits: {
      messages_truncated: 0,
      telemetry_truncated: 0,
      event_refs_truncated: 0,
      trace_refs_truncated: 0,
    },
  };
}

function telemetry(
  stepId: string,
  occurredAt: number,
  overrides: Partial<CaseFlowEdgeTraceTelemetryEntry> = {},
): CaseFlowEdgeTraceTelemetryEntry {
  return {
    event_ref: `event-${occurredAt}`,
    trace_ref: `trace-${occurredAt}`,
    agent_run_ref: null,
    correlation_ref: null,
    causation_ref: null,
    event_type: 'workflow.step.updated',
    step_id: stepId,
    sequence: occurredAt,
    occurred_at: occurredAt,
    status: 'running',
    duration_ms: null,
    model: null,
    provider: null,
    token_usage: null,
    cost_micros: null,
    tool: null,
    error: null,
    redaction_policy: 'user',
    ...overrides,
  };
}
