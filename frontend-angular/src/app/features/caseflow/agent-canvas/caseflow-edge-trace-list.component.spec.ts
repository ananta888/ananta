import { ɵresolveComponentResources } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import type { CaseFlowEdgeTraceTelemetryEntry } from './caseflow-edge-trace.models';
import { CaseFlowEdgeTraceListComponent } from './caseflow-edge-trace-list.component';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('CaseFlowEdgeTraceListComponent', () => {
  let fixture: ComponentFixture<CaseFlowEdgeTraceListComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [CaseFlowEdgeTraceListComponent] }).compileComponents();
    fixture = TestBed.createComponent(CaseFlowEdgeTraceListComponent);
  });

  it('renders only the allowlisted read model fields and explicit unavailable values', () => {
    fixture.componentRef.setInput('entries', [telemetryEntry()]);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('workflow.edge.message.sent');
    expect(text).toContain('event-a');
    expect(text).toContain('input_tokens: 3');
    expect(text).toContain('Nicht verfügbar');
    expect(fixture.nativeElement.querySelector('[data-telemetry-index="0"]')).not.toBeNull();
  });

  it('highlights only the requested positional entry without manufacturing a trace reference', () => {
    const missingReferences = { ...telemetryEntry(), event_ref: null, trace_ref: null };
    fixture.componentRef.setInput('entries', [telemetryEntry(), missingReferences]);
    fixture.componentRef.setInput('highlightedIndex', 1);
    fixture.detectChanges();

    const entries = fixture.nativeElement.querySelectorAll('.trace-entry');
    expect(entries[0].classList.contains('highlighted')).toBe(false);
    expect(entries[1].classList.contains('highlighted')).toBe(true);
    expect(entries[1].textContent).toContain('Nicht verfügbar');
  });
});

function telemetryEntry(): CaseFlowEdgeTraceTelemetryEntry {
  return {
    event_ref: 'event-a',
    trace_ref: 'trace-a',
    agent_run_ref: null,
    correlation_ref: null,
    causation_ref: null,
    event_type: 'workflow.edge.message.sent',
    step_id: 'agent-b',
    sequence: 1,
    occurred_at: 12,
    status: 'active',
    duration_ms: null,
    model: null,
    provider: null,
    token_usage: { input_tokens: 3 },
    cost_micros: null,
    tool: null,
    error: null,
    redaction_policy: 'user',
  };
}
