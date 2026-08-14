import { HttpErrorResponse } from '@angular/common/http';
import { ɵresolveComponentResources } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { Observable, Subject, throwError } from 'rxjs';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { CaseFlowAgentEdgeInspectorComponent } from './caseflow-agent-edge-inspector.component';
import { CaseFlowEdgeTraceApiService } from './caseflow-edge-trace-api.service';
import type {
  CaseFlowEdgeIdentity,
  CaseFlowEdgeTraceProjection,
  CaseFlowEdgeTraceReadModel,
  CaseFlowEdgeTraceTelemetryEntry,
} from './caseflow-edge-trace.models';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('CaseFlowAgentEdgeInspectorComponent', () => {
  const responses: Subject<CaseFlowEdgeTraceReadModel>[] = [];
  let api: { read: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    responses.length = 0;
    api = {
      read: vi.fn((): Observable<CaseFlowEdgeTraceReadModel> => {
        const response = new Subject<CaseFlowEdgeTraceReadModel>();
        responses.push(response);
        return response.asObservable();
      }),
    };
    await TestBed.configureTestingModule({
      imports: [CaseFlowAgentEdgeInspectorComponent],
      providers: [{ provide: CaseFlowEdgeTraceApiService, useValue: api }],
    }).compileComponents();
  });

  afterEach(() => vi.restoreAllMocks());

  it('renders messages only from the exact selected source-to-target edge', () => {
    const fixture = createFixture();
    responses[0].next(readModel([
      edgeProjection(FORWARD, 'forward-only'),
      edgeProjection(REVERSE, 'reverse-only'),
    ]));
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('forward-only');
    expect(fixture.nativeElement.textContent).not.toContain('reverse-only');
    expect(api.read).toHaveBeenCalledWith({ workflow_id: 'workflow-a', run_id: 'run-a' });
  });

  it('does not offer an unrelated edge as a reverse direction', () => {
    const fixture = TestBed.createComponent(CaseFlowAgentEdgeInspectorComponent);
    fixture.componentRef.setInput('workflowId', 'workflow-a');
    fixture.componentRef.setInput('runId', 'run-a');
    fixture.componentRef.setInput('edge', FORWARD);
    fixture.componentRef.setInput('reverseEdge', {
      edge_id: 'edge-b-c', source_step_id: 'agent-b', target_step_id: 'agent-c',
    });
    fixture.detectChanges();

    expect(fixture.componentInstance.availableDirections).toEqual([FORWARD]);
    expect(fixture.nativeElement.querySelector('.direction-switch')).toBeNull();
  });

  it('projects a host-managed read model synchronously without an API request', () => {
    const model = readModel([
      edgeProjection(FORWARD, 'host-forward'),
      edgeProjection(REVERSE, 'host-reverse'),
    ]);
    const fixture = createHostFixture(model);

    expect(api.read).not.toHaveBeenCalled();
    expect(fixture.componentInstance.projection?.edge_id).toBe('edge-a-b');
    expect(fixture.nativeElement.textContent).toContain('host-forward');

    const reverse = fixture.nativeElement.querySelectorAll(
      '.direction-switch button',
    )[1] as HTMLButtonElement;
    reverse.click();

    expect(api.read).not.toHaveBeenCalled();
    expect(fixture.componentInstance.loading).toBe(false);
    expect(fixture.componentInstance.projection?.edge_id).toBe('edge-b-a');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('host-reverse');
    expect(fixture.nativeElement.textContent).not.toContain('host-forward');
  });

  it('preserves the exact reverse direction and telemetry tab across host refreshes', () => {
    const fixture = createHostFixture(readModel([
      edgeProjection(FORWARD, 'forward-v1'),
      edgeProjection(REVERSE, 'reverse-v1'),
    ]));
    const reverseButton = fixture.nativeElement.querySelectorAll(
      '.direction-switch button',
    )[1] as HTMLButtonElement;
    reverseButton.click();
    fixture.detectChanges();
    const telemetryTab = fixture.nativeElement.querySelector(
      '[data-inspector-tab="telemetry"]',
    ) as HTMLButtonElement;
    telemetryTab.click();
    fixture.detectChanges();

    fixture.componentRef.setInput('traceReadModel', readModel([
      edgeProjection(FORWARD, 'forward-v2'),
      edgeProjection(REVERSE, 'reverse-v2'),
    ]));
    fixture.detectChanges();

    expect(api.read).not.toHaveBeenCalled();
    expect(fixture.componentInstance.selectedDirection).toEqual(REVERSE);
    expect(fixture.componentInstance.projection?.edge_id).toBe(REVERSE.edge_id);
    expect(fixture.componentInstance.projection?.messages[0]?.content).toBe('reverse-v2');
    expect(fixture.componentInstance.activeTab).toBe('telemetry');
    expect(fixture.nativeElement.querySelector('[role="tabpanel"]')?.getAttribute('aria-labelledby'))
      .toBe(fixture.componentInstance.telemetryTabId);
  });

  it('treats an explicit null host model as settled and performs no API request', () => {
    const fixture = createHostFixture(null);

    expect(api.read).not.toHaveBeenCalled();
    expect(fixture.componentInstance.loading).toBe(false);
    expect(fixture.componentInstance.projection).toBeNull();
    expect(fixture.componentInstance.errorCode).toBeNull();
  });

  it('clears the old direction synchronously and ignores the cancelled response', () => {
    const fixture = createFixture();
    const selected: string[] = [];
    fixture.componentInstance.directionSelected.subscribe(edge => {
      expect(fixture.componentInstance.projection).toBeNull();
      expect(fixture.componentInstance.loading).toBe(true);
      selected.push(edge.edge_id);
    });
    responses[0].next(readModel([edgeProjection(FORWARD, 'old-direction')]));
    fixture.detectChanges();
    expect(fixture.componentInstance.projection?.edge_id).toBe('edge-a-b');

    const buttons = fixture.nativeElement.querySelectorAll('.direction-switch button');
    buttons[1].click();

    expect(fixture.componentInstance.projection).toBeNull();
    expect(fixture.componentInstance.loading).toBe(true);
    expect(selected).toEqual(['edge-b-a']);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).not.toContain('old-direction');

    responses[0].next(readModel([edgeProjection(FORWARD, 'stale-response')]));
    responses[1].next(readModel([edgeProjection(REVERSE, 'new-direction')]));
    fixture.detectChanges();

    expect(fixture.componentInstance.projection?.edge_id).toBe('edge-b-a');
    expect(fixture.nativeElement.textContent).toContain('new-direction');
    expect(fixture.nativeElement.textContent).not.toContain('stale-response');
  });

  it.each([401, 403, 404])('clears fail-closed after an HTTP %s response', status => {
    api.read.mockImplementationOnce(() => throwError(() => new HttpErrorResponse({ status })));
    const fixture = createFixture();

    expect(fixture.componentInstance.projection).toBeNull();
    expect(fixture.componentInstance.loading).toBe(false);
    expect(fixture.componentInstance.errorCode).toMatch(/unauthorized|forbidden|not_found/);
    expect(fixture.nativeElement.textContent).toContain('Keine autorisierte Edge-Projektion');
  });

  it('opens the one exactly correlated telemetry entry from a message', () => {
    const fixture = createFixture();
    responses[0].next(readModel([edgeProjection(FORWARD, 'correlated-message')]));
    fixture.detectChanges();

    const correlation = fixture.nativeElement.querySelector('.correlation-link') as HTMLButtonElement;
    expect(correlation.textContent).toContain('trace-edge-a-b');
    correlation.click();
    fixture.detectChanges();

    expect(fixture.componentInstance.activeTab).toBe('telemetry');
    expect(fixture.componentInstance.highlightedTelemetryIndex).toBe(0);
    expect(fixture.nativeElement.querySelector('.trace-entry.highlighted')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('[data-testid="caseflow-edge-run-scope"]')?.textContent)
      .toContain('run-a');
  });

  it('implements labelled roving tabs with Arrow, Home, and End keyboard control', () => {
    const fixture = createHostFixture(readModel([edgeProjection(FORWARD, 'tabs')]));
    const tabs = fixture.nativeElement.querySelectorAll('[role="tab"]') as NodeListOf<HTMLButtonElement>;
    const communication = tabs[0];
    const telemetryTab = tabs[1];

    expect(communication.getAttribute('tabindex')).toBe('0');
    expect(telemetryTab.getAttribute('tabindex')).toBe('-1');
    expect(communication.getAttribute('aria-controls')).toBe(
      fixture.componentInstance.communicationPanelId,
    );
    expect(fixture.nativeElement.querySelector('[role="tabpanel"]')?.getAttribute('aria-labelledby'))
      .toBe(fixture.componentInstance.communicationTabId);

    communication.focus();
    communication.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'ArrowRight', bubbles: true, cancelable: true,
    }));
    fixture.detectChanges();
    expect(fixture.componentInstance.activeTab).toBe('telemetry');
    expect(document.activeElement).toBe(telemetryTab);
    expect(telemetryTab.getAttribute('tabindex')).toBe('0');
    expect(fixture.nativeElement.querySelector('[role="tabpanel"]')?.getAttribute('aria-labelledby'))
      .toBe(fixture.componentInstance.telemetryTabId);

    telemetryTab.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Home', bubbles: true, cancelable: true,
    }));
    fixture.detectChanges();
    expect(fixture.componentInstance.activeTab).toBe('communication');
    expect(document.activeElement).toBe(communication);

    communication.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'End', bubbles: true, cancelable: true,
    }));
    expect(fixture.componentInstance.activeTab).toBe('telemetry');
    expect(document.activeElement).toBe(telemetryTab);
  });

  it('defines a visible keyboard focus indicator', async () => {
    const styles = await readFile(
      resolve(
        process.cwd(),
        'src/app/features/caseflow/agent-canvas/caseflow-agent-edge-inspector.component.scss',
      ),
      'utf8',
    );

    expect(styles).toMatch(/button:focus-visible\s*\{[^}]*outline:\s*2px solid/s);
    expect(styles).toContain('outline-offset: 2px');
  });

  it('marks ambiguous correlation unverified and performs no local persistence write', () => {
    const localWrite = vi.spyOn(Storage.prototype, 'setItem');
    const fixture = createFixture();
    const projected = edgeProjection(FORWARD, 'ambiguous-message');
    const duplicateTelemetry = projected.telemetry[0];
    responses[0].next(readModel([{ ...projected, telemetry: [duplicateTelemetry, duplicateTelemetry] }]));
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.correlation-link')).toBeNull();
    expect(fixture.nativeElement.textContent).toContain('Korrelation nicht verifiziert');
    expect(localWrite).not.toHaveBeenCalled();
    expect(api).not.toHaveProperty('save');
  });
});

const FORWARD: CaseFlowEdgeIdentity = {
  edge_id: 'edge-a-b', source_step_id: 'agent-a', target_step_id: 'agent-b',
};
const REVERSE: CaseFlowEdgeIdentity = {
  edge_id: 'edge-b-a', source_step_id: 'agent-b', target_step_id: 'agent-a',
};

function createFixture(): ComponentFixture<CaseFlowAgentEdgeInspectorComponent> {
  const fixture = TestBed.createComponent(CaseFlowAgentEdgeInspectorComponent);
  fixture.componentRef.setInput('workflowId', 'workflow-a');
  fixture.componentRef.setInput('runId', 'run-a');
  fixture.componentRef.setInput('edge', FORWARD);
  fixture.componentRef.setInput('reverseEdge', REVERSE);
  fixture.detectChanges();
  return fixture;
}

function createHostFixture(
  model: CaseFlowEdgeTraceReadModel | null,
): ComponentFixture<CaseFlowAgentEdgeInspectorComponent> {
  const fixture = TestBed.createComponent(CaseFlowAgentEdgeInspectorComponent);
  fixture.componentRef.setInput('workflowId', 'workflow-a');
  fixture.componentRef.setInput('runId', 'run-a');
  fixture.componentRef.setInput('edge', FORWARD);
  fixture.componentRef.setInput('reverseEdge', REVERSE);
  fixture.componentRef.setInput('traceReadModel', model);
  fixture.detectChanges();
  return fixture;
}

function readModel(edges: readonly CaseFlowEdgeTraceProjection[]): CaseFlowEdgeTraceReadModel {
  return {
    schema: 'ananta.caseflow_edge_trace_read_model.v1',
    workflow_id: 'workflow-a',
    run_id: 'run-a',
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

function edgeProjection(
  identity: Readonly<CaseFlowEdgeIdentity>,
  content: string,
): CaseFlowEdgeTraceProjection {
  const traceRef = `trace-${identity.edge_id}`;
  const eventRef = `event-${identity.edge_id}`;
  return {
    ...identity,
    edge_kind: 'dependency',
    activity_status: 'active',
    verification_status: 'verified',
    reason_code: 'caseflow_edge_correlation_verified_active',
    correlation_basis: 'explicit_edge_id',
    event_refs: [eventRef],
    trace_refs: [traceRef],
    messages: [{
      content,
      role: 'assistant',
      event_ref: eventRef,
      trace_ref: traceRef,
      correlation_ref: traceRef,
      occurred_at: 12,
      verification_status: 'verified',
      truncated: false,
    }],
    telemetry: [telemetry(eventRef, traceRef, identity.target_step_id)],
    limits: {
      messages_truncated: 0,
      telemetry_truncated: 0,
      event_refs_truncated: 0,
      trace_refs_truncated: 0,
    },
  };
}

function telemetry(
  eventRef: string,
  traceRef: string,
  stepId: string,
): CaseFlowEdgeTraceTelemetryEntry {
  return {
    event_ref: eventRef,
    trace_ref: traceRef,
    agent_run_ref: null,
    correlation_ref: null,
    causation_ref: null,
    event_type: 'workflow.edge.message.sent',
    step_id: stepId,
    sequence: 1,
    occurred_at: 12,
    status: 'active',
    duration_ms: null,
    model: null,
    provider: null,
    token_usage: null,
    cost_micros: null,
    tool: null,
    error: null,
    redaction_policy: 'user',
  };
}
