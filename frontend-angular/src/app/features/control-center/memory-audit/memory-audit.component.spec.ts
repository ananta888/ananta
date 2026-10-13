import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';

import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { MemoryAuditApiService } from './memory-audit-api.service';
import { MemoryAuditComponent } from './memory-audit.component';
import { MemoryAuditPage, MemoryDiffPage } from './memory-audit.models';

function page(projectId = 'project-a', next: number | null = null): MemoryAuditPage {
  return {
    schema: 'ananta.memory-audit-page.v1', project_id: projectId, next_after_sequence: next,
    integrity: 'event_digests_verified', complete_chain_verified: false,
    items: [{
      event_id: `event-${projectId}`, sequence: 1, event_hash: 'a'.repeat(64), previous_hash: '0'.repeat(64),
      created_at: 100, retention_until: 3700, actor_subject: 'worker', run_id: 'run-a', trace_id: 'trace-a',
      operation: 'read', allowed: false, rule_id: 'memory.explicit-capability.v1',
      reason_code: 'memory_capability_missing', policy_revision: 'policy-a', policy_digest: 'b'.repeat(64),
      channel_id: 'run-scratchpad', namespace: { task_id: 'task-a' },
      security: { scope: 'run', sensitivity: 'internal', trust_level: 'untrusted', retention_class: 'ephemeral' },
      event_kind: 'policy_decision',
    }],
  };
}

describe('MemoryAuditComponent', () => {
  let selected: BehaviorSubject<string>;
  let api: { events: ReturnType<typeof vi.fn>; diffs: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    selected = new BehaviorSubject('project-a');
    api = { events: vi.fn(() => of(page())), diffs: vi.fn() };
    await TestBed.configureTestingModule({
      imports: [MemoryAuditComponent],
      providers: [
        { provide: ControlCenterStateFacade, useValue: { selectedProjectId$: selected, hubBaseUrl: () => 'http://hub.test' } },
        { provide: MemoryAuditApiService, useValue: api },
      ],
    }).compileComponents();
  });

  it('shows actual Hub decisions, scope, trust and policy without claiming complete chain verification', () => {
    const fixture = TestBed.createComponent(MemoryAuditComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Abgelehnt');
    expect(fixture.nativeElement.textContent).toContain('memory_capability_missing');
    expect(fixture.nativeElement.textContent).toContain('untrusted');
    expect(fixture.nativeElement.textContent).toContain('policy-a');
    expect(fixture.nativeElement.textContent).toContain('nicht enthalten');
    expect(api.events).toHaveBeenCalledWith('http://hub.test', 'project-a', {}, 0);
  });

  it('cancels the old project request and never renders its late response', () => {
    const old = new Subject<MemoryAuditPage>();
    api.events.mockReturnValueOnce(old).mockReturnValueOnce(of(page('project-b')));
    const fixture = TestBed.createComponent(MemoryAuditComponent);
    fixture.detectChanges();
    selected.next('project-b');
    old.next(page('project-a'));
    fixture.detectChanges();
    expect(fixture.componentInstance.events.map(event => event.event_id)).toEqual(['event-project-b']);
    expect(fixture.componentInstance.loading).toBe(false);
  });

  it('keeps the original filters across subsequent pages and deduplicates events', () => {
    api.events.mockReturnValue(of(page('project-a', 1)));
    const fixture = TestBed.createComponent(MemoryAuditComponent);
    fixture.detectChanges();
    fixture.componentInstance.filters.run_id = ' run-a ';
    fixture.componentInstance.load();
    fixture.componentInstance.filters.run_id = 'unapplied-new-filter';
    fixture.componentInstance.load(false);
    expect(api.events).toHaveBeenLastCalledWith('http://hub.test', 'project-a', { run_id: 'run-a' }, 1);
    expect(fixture.componentInstance.events).toHaveLength(1);
  });

  it('clears previous project results when the new project denies access', () => {
    const fixture = TestBed.createComponent(MemoryAuditComponent);
    fixture.detectChanges();
    api.events.mockReturnValue(throwError(() => new Error('403')));
    selected.next('project-b');
    fixture.detectChanges();
    expect(fixture.componentInstance.events).toEqual([]);
    expect(fixture.nativeElement.textContent).toContain('Projekt-Verwaltungsrechte');
  });

  it('refuses a response bound to another project', () => {
    api.events.mockReturnValue(of(page('foreign-project')));
    const fixture = TestBed.createComponent(MemoryAuditComponent);
    fixture.detectChanges();
    expect(fixture.componentInstance.events).toEqual([]);
    expect(fixture.componentInstance.error).toContain('passt nicht zum Projekt');
  });

  it('renders verified diffs including changes from a failed memory session', () => {
    const result: MemoryDiffPage = {
      schema: 'ananta.memory-diff-page.v1', project_id: 'project-a', run_id: 'run-a',
      integrity: 'snapshot_and_audit_links_verified', production_release_evidence: false,
      items: [{ run_id: 'run-a', channel_id: 'hub-result-memory', scope: 'task', status: 'complete',
        scope_outcome: 'failed', attribution: 'observed_scope_change', before_snapshot_id: 'snapshot-before',
        after_snapshot_id: 'snapshot-after', audit_event_ids: ['audit-before', 'audit-after'],
        changes: [{ entry_id: 'entry-added', kind: 'added', before: null, after: {
          entry_id: 'entry-added', content_digest: 'c'.repeat(64), metadata_digest: 'd'.repeat(64),
          security: page().items[0].security,
        } }],
      }],
    };
    api.diffs.mockReturnValue(of(result));
    const fixture = TestBed.createComponent(MemoryAuditComponent);
    fixture.detectChanges();
    fixture.componentInstance.view = 'diffs';
    fixture.componentInstance.filters.run_id = 'run-a';
    fixture.componentInstance.load();
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('entry-added');
    expect(text).toContain('mit Fehler beendet');
    expect(text).toContain('audit-after');
    expect(text).toContain('parallele Schreibvorgänge');
    expect(api.diffs).toHaveBeenCalledWith('http://hub.test', 'project-a', { run_id: 'run-a' });
  });

  it('does not query a diff without an explicit Run', () => {
    const fixture = TestBed.createComponent(MemoryAuditComponent);
    fixture.detectChanges();
    fixture.componentInstance.view = 'diffs';
    fixture.componentInstance.load();
    expect(api.diffs).not.toHaveBeenCalled();
    expect(fixture.componentInstance.error).toContain('Run angeben');
    expect(fixture.componentInstance.events).toEqual([]);
  });

  it('never accepts an event page as a verified diff', () => {
    api.diffs.mockReturnValue(of(page()));
    const fixture = TestBed.createComponent(MemoryAuditComponent);
    fixture.detectChanges();
    fixture.componentInstance.view = 'diffs';
    fixture.componentInstance.filters.run_id = 'run-a';
    fixture.componentInstance.load();
    expect(fixture.componentInstance.diffs).toEqual([]);
    expect(fixture.componentInstance.error).not.toBe('');
  });
});
