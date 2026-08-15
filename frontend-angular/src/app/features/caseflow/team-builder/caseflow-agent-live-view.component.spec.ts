/**
 * Watching a team work.
 *
 * The evidence on this screen comes from the Hub, so the tests drive a real
 * runtime overlay and a real edge-trace read model through the real
 * projections. What is pinned is that a person sees who is running, what
 * each agent produced, and who it said it to — and that an unproven run
 * shows as unproven instead of as an empty, confident-looking team.
 */

import { signal, ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { of, throwError } from 'rxjs';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  VisualProcessApiService,
  type VpGraph,
  type VpRuntimeOverlay,
} from '../../visual-process/visual-process-api.service';
import type { CaseFlowEdgeTraceReadModel } from '../agent-canvas/caseflow-edge-trace.models';
import { CaseFlowAgentRuntimeSessionFacade } from '../agent-canvas/caseflow-agent-runtime-session.facade';
import { CaseFlowAgentLiveViewComponent } from './caseflow-agent-live-view.component';

beforeAll(async () => {
  await ɵresolveComponentResources(async resource => {
    const name = resource.split('/').at(-1) || resource;
    const candidates = [
      new URL(resource, import.meta.url),
      resolve(process.cwd(), 'src/app/features/caseflow/team-builder', name),
      resolve(process.cwd(), 'src/app/features/caseflow/agent-canvas', name),
    ];
    for (const candidate of candidates) {
      try {
        return await readFile(candidate, 'utf8');
      } catch {
        // Angular reports relative URLs only; try the owning directory.
      }
    }
    throw new Error(`Component resource not found: ${resource}`);
  });
});

const GRAPH_ID = 'team-1';
const RUN_ID = 'run-1';

function graph(): VpGraph {
  return {
    id: GRAPH_ID,
    name: 'Mein Team',
    description: '',
    version: '1.0.0',
    tags: [],
    definition_revision: 3,
    base_graph_hash: 'a'.repeat(64),
    steps: [
      step('mara', 'Mara', 'developer'),
      step('fritz', 'Fritz', 'tester'),
    ],
    edges: [{ id: 'e-1', source: 'mara', target: 'fritz', condition: { kind: 'always' } }],
  } as unknown as VpGraph;
}

function step(id: string, label: string, role: string) {
  return {
    id,
    label,
    kind: 'coding',
    role,
    io: { inputs: [], outputs: [] },
    position: { x: 100, y: 100 },
    policy_hints: [],
    gate: false,
  };
}

function overlay(): VpRuntimeOverlay {
  return {
    run_id: RUN_ID,
    workflow_id: GRAPH_ID,
    overall_status: 'running',
    current_step_ids: ['mara'],
    updated_at: 1000,
    steps: {
      mara: { step_id: 'mara', status: 'running', selected_model: 'llama-3', duration_ms: 2400 },
      fritz: { step_id: 'fritz', status: 'pending' },
    },
  } as unknown as VpRuntimeOverlay;
}

function readModel(messages: readonly Record<string, unknown>[]): CaseFlowEdgeTraceReadModel {
  return {
    schema: 'ananta.caseflow_edge_trace_read_model.v1',
    workflow_id: GRAPH_ID,
    run_id: RUN_ID,
    catalog_verification_status: 'verified',
    verification_status: 'verified',
    reason_code: '',
    source_revision: 7,
    telemetry: {
      source_event_count: 1,
      processed_event_count: 1,
      rejected_event_count: 0,
      truncated_event_count: 0,
      correlated_edge_count: 1,
      redaction_policy: 'user',
      messages_per_edge_limit: 50,
      telemetry_per_edge_limit: 50,
    },
    edges: [
      {
        edge_id: 'e-1',
        source_step_id: 'mara',
        target_step_id: 'fritz',
        edge_kind: 'dependency',
        activity_status: 'active',
        verification_status: 'verified',
        reason_code: '',
        correlation_basis: 'explicit_edge_id',
        event_refs: [],
        trace_refs: [],
        messages,
        telemetry: [],
        limits: {
          messages_truncated: 0,
          telemetry_truncated: 0,
          event_refs_truncated: 0,
          trace_refs_truncated: 0,
        },
      },
    ],
  } as unknown as CaseFlowEdgeTraceReadModel;
}

function message(content: string, overrides: Record<string, unknown> = {}) {
  return {
    content,
    role: 'assistant',
    event_ref: null,
    trace_ref: null,
    correlation_ref: null,
    occurred_at: 1,
    verification_status: 'verified',
    truncated: false,
    ...overrides,
  };
}

/** A session stub, so the tests drive the projections rather than the poller. */
class FakeSession {
  readonly graphId = signal<string | null>(null);
  readonly workflowId = signal<string | null>(null);
  readonly runId = signal<string | null>(null);
  readonly revision = signal<number | null>(null);
  readonly runtimeOverlay = signal<VpRuntimeOverlay | null>(null);
  readonly edgeTraceReadModel = signal<CaseFlowEdgeTraceReadModel | null>(null);
  readonly state = signal<string>('detached');
  readonly errorCode = signal<string | null>(null);
  readonly attached: { graph_id: string; workflow_id: string }[] = [];
  detached = 0;
  refreshed = 0;

  attach(scope: { graph_id: string; workflow_id: string }): void {
    this.attached.push(scope);
    this.state.set('loading');
  }
  detach(): void {
    this.detached += 1;
  }
  refresh(): void {
    this.refreshed += 1;
  }
  canRefresh(): boolean {
    return ['no_run', 'no_run_timeout', 'active', 'error'].includes(this.state());
  }

  /** Put the stub into the state a live run produces. */
  goLive(messages: readonly Record<string, unknown>[] = [message('Ich schreibe den Test.')]): void {
    this.runId.set(RUN_ID);
    this.revision.set(7);
    this.runtimeOverlay.set(overlay());
    this.edgeTraceReadModel.set(readModel(messages));
    this.state.set('active');
  }
}

let session: FakeSession;
let api: { saveGraph: ReturnType<typeof vi.fn> };

function mount(input: VpGraph = graph()) {
  const fixture = TestBed.createComponent(CaseFlowAgentLiveViewComponent);
  fixture.componentRef.setInput('graph', input);
  fixture.detectChanges();
  return fixture;
}

function text(fixture: ReturnType<typeof mount>): string {
  return fixture.nativeElement.textContent as string;
}

function openAgent(fixture: ReturnType<typeof mount>, stepId = 'mara') {
  fixture.componentInstance['selectAgent'](stepId);
  fixture.detectChanges();
}

beforeEach(() => {
  session = new FakeSession();
  api = { saveGraph: vi.fn().mockReturnValue(of({ definition_revision: 4, base_graph_hash: 'b'.repeat(64) })) };
  TestBed.configureTestingModule({
    imports: [CaseFlowAgentLiveViewComponent],
    providers: [provideRouter([]), { provide: VisualProcessApiService, useValue: api }],
  });
  TestBed.overrideProvider(CaseFlowAgentRuntimeSessionFacade, { useValue: session });
});

describe('attaching to a run', () => {
  it('scopes the session to the graph it is showing', () => {
    mount();

    expect(session.attached).toEqual([{ graph_id: GRAPH_ID, workflow_id: GRAPH_ID }]);
  });

  it('detaches rather than attaching to a graph with no identity', () => {
    mount({ ...graph(), id: '' } as VpGraph);

    expect(session.attached).toEqual([]);
    expect(session.detached).toBe(1);
  });

  it('says in plain words that the team is not running right now', () => {
    const fixture = mount();

    session.state.set('no_run');
    fixture.detectChanges();

    expect(text(fixture)).toContain('läuft gerade nicht');
  });

  it('surfaces a runtime that cannot be read instead of showing an empty team', () => {
    const fixture = mount();

    session.state.set('error');
    session.errorCode.set('caseflow_runtime_status_unavailable');
    fixture.detectChanges();

    expect(text(fixture)).toContain('Laufzeit nicht lesbar');
    expect(text(fixture)).toContain('caseflow_runtime_status_unavailable');
  });

  it('offers a retry only when one can still target the attached run', () => {
    const fixture = mount();

    session.state.set('detached');
    fixture.detectChanges();
    expect(text(fixture)).not.toContain('Aktualisieren');

    session.state.set('active');
    fixture.detectChanges();
    expect(text(fixture)).toContain('Aktualisieren');
  });
});

describe('the open agent', () => {
  it('shows nothing opened until a person clicks one', () => {
    const fixture = mount();

    expect(text(fixture)).toContain('Einen Agenten auf der Karte anklicken');
    expect(fixture.debugElement.query(By.css('.agent-box'))).toBeNull();
  });

  it('opens an agent with a glyph, its role and what it is doing', () => {
    session.goLive();
    const fixture = mount();

    openAgent(fixture);

    expect(text(fixture)).toContain('Rolle: developer');
    expect(text(fixture)).toContain('Modell: llama-3');
    expect(text(fixture)).toContain('arbeitet');
    expect(fixture.debugElement.query(By.css('.agent-glyph')).nativeElement.textContent.trim()).toBeTruthy();
  });

  it('reports a waiting agent apart from a working one', () => {
    session.goLive();
    const fixture = mount();

    openAgent(fixture, 'fritz');

    expect(text(fixture)).toContain('wartet');
  });

  it('ignores a click on a step the graph does not have', () => {
    const fixture = mount();

    openAgent(fixture, 'nobody');

    expect(fixture.debugElement.query(By.css('.agent-box'))).toBeNull();
  });
});

describe('thoughts and exchange', () => {
  it('shows what the open agent produced under its own tab', () => {
    session.goLive();
    const fixture = mount();

    openAgent(fixture);

    expect(text(fixture)).toContain('Gedanken (1)');
    expect(text(fixture)).toContain('Ich schreibe den Test.');
  });

  it('offers one tab per agent it exchanged with', () => {
    session.goLive();
    const fixture = mount();

    openAgent(fixture);

    const tabs = fixture.debugElement.queryAll(By.css('.agent-tabs button')).map(tab =>
      tab.nativeElement.textContent.trim(),
    );
    expect(tabs[0]).toContain('Gedanken');
    expect(tabs[1]).toContain('Fritz');
  });

  it('switches to the exchange with one peer when its tab is chosen', () => {
    session.goLive([message('An dich, Fritz.')]);
    const fixture = mount();
    openAgent(fixture);

    fixture.componentInstance['tab'].set('fritz');
    fixture.detectChanges();

    expect(text(fixture)).toContain('An dich, Fritz.');
    expect(fixture.debugElement.queryAll(By.css('.chat-line'))).toHaveLength(1);
  });

  it('does not credit the receiving agent with what was said to it', () => {
    session.goLive([message('Von Mara.')]);
    const fixture = mount();

    openAgent(fixture, 'fritz');

    expect(text(fixture)).toContain('Gedanken (0)');
    expect(text(fixture)).toContain('Hier ist noch nichts gesagt worden.');
  });

  it('marks a message whose origin the Hub could not prove', () => {
    session.goLive([message('unbelegt', { verification_status: 'unverified', truncated: true })]);
    const fixture = mount();

    openAgent(fixture);

    expect(text(fixture)).toContain('ungeprüft');
    expect(text(fixture)).toContain('gekürzt');
  });

  it('explains the absence rather than showing an empty chat with no run', () => {
    const fixture = mount();

    openAgent(fixture);

    expect(text(fixture)).toContain('Noch kein Lauf zu diesem Team lesbar');
  });
});

describe('giving an agent a name', () => {
  it('persists the name through the same graph save as any other edit', () => {
    const fixture = mount();
    openAgent(fixture);

    fixture.componentInstance['draftName'].set('Fritzchen');
    fixture.detectChanges();
    fixture.debugElement.query(By.css('.live-primary')).nativeElement.click();

    const saved = api.saveGraph.mock.calls[0][0] as VpGraph;
    expect(saved.steps.find(item => item.id === 'mara')?.label).toBe('Fritzchen');
    expect(saved.definition_revision).toBe(3);
  });

  it('hands the caller the revision the save established', () => {
    const fixture = mount();
    let emitted: VpGraph | null = null;
    fixture.componentInstance.graphChange.subscribe(value => (emitted = value));
    openAgent(fixture);
    fixture.componentInstance['draftName'].set('Fritzchen');
    fixture.detectChanges();

    fixture.debugElement.query(By.css('.live-primary')).nativeElement.click();

    expect(emitted!.definition_revision).toBe(4);
    expect(emitted!.base_graph_hash).toBe('b'.repeat(64));
  });

  it('refuses a name another agent in the same team already answers to', () => {
    const fixture = mount();
    openAgent(fixture);

    fixture.componentInstance['draftName'].set('Fritz');
    fixture.detectChanges();

    expect(fixture.debugElement.query(By.css('.live-primary')).nativeElement.disabled).toBe(true);
  });

  it('refuses a blank name and an unchanged one alike', () => {
    const fixture = mount();
    openAgent(fixture);

    for (const value of ['   ', 'Mara']) {
      fixture.componentInstance['draftName'].set(value);
      fixture.detectChanges();
      expect(fixture.debugElement.query(By.css('.live-primary')).nativeElement.disabled).toBe(true);
    }
  });

  it('says so when the name could not be saved', () => {
    api.saveGraph.mockReturnValue(throwError(() => new Error('conflict')));
    const fixture = mount();
    openAgent(fixture);
    fixture.componentInstance['draftName'].set('Fritzchen');
    fixture.detectChanges();

    fixture.debugElement.query(By.css('.live-primary')).nativeElement.click();
    fixture.detectChanges();

    expect(text(fixture)).toContain('Der Name konnte nicht gespeichert werden.');
  });
});
