import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { SpreadsheetStudioApiService } from './spreadsheet-studio-api.service';
import { SpreadsheetStudioPageComponent } from './spreadsheet-studio-page.component';
import { SpreadsheetDocument, SpreadsheetProposalResult } from './spreadsheet-studio.models';

function document(version = 1): SpreadsheetDocument {
  return {
    schema: 'ananta.spreadsheet-document-version.v1',
    document_id: 'document-one',
    title: 'Workbook',
    version,
    snapshot: {
      schema: 'ananta.spreadsheet-workbook-snapshot.v1',
      snapshot_id: `snapshot-${version}`,
      document_version_id: `version-${version}`,
      sheets: [{ sheet_id: 'sheet-one', name: 'Sheet 1', hidden: false, cells: [] }],
    },
    snapshot_digest: String(version).repeat(64),
    state: 'published',
    unsupported_objects: [],
    source_grounding_verified: false,
    human_intervention_required: false,
  };
}

function result(state: 'candidate_ready' | 'promoted'): SpreadsheetProposalResult {
  return {
    proposal_id: `proposal-${state}`,
    proposal_digest: 'a'.repeat(64),
    document_id: 'document-one',
    base_version: 1,
    base_snapshot_digest: '1'.repeat(64),
    actions: [],
    state,
    promoted_version: state === 'promoted' ? 2 : null,
    candidate_snapshot_digest: '2'.repeat(64),
    diff: [],
    actual_diff: { total: 0, has_more: false, diff_digest: 'b'.repeat(64), items: [] },
    validation: { passed: true, reason_codes: [], results: [] },
    reason_codes: [],
    production_fidelity: true,
    human_intervention_required: false,
  };
}

describe('SpreadsheetStudioPageComponent', () => {
  afterEach(() => vi.useRealTimers());

  it('supports a fully automatic candidate-to-promotion path without bypassing review gates', () => {
    const api = {
      execute: vi.fn()
        .mockReturnValueOnce(of(result('candidate_ready')))
        .mockReturnValueOnce(of(result('promoted'))),
      list: vi.fn().mockReturnValue(of({ items: [document(2)], limit: 100 })),
    };
    TestBed.configureTestingModule({
      providers: [
        { provide: SpreadsheetStudioApiService, useValue: api },
        { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub.test' }] } },
      ],
    });
    const component = TestBed.runInInjectionContext(() => new SpreadsheetStudioPageComponent());
    component.selected = document();
    component.documents = [document()];
    component.viewerSynchronized = true;
    component.automaticApply = true;
    component.cell = 'A1';
    component.value = '42';

    component.execute();

    expect(api.execute).toHaveBeenCalledTimes(2);
    expect(api.execute.mock.calls[0][1]).toMatchObject({ automatic_promotion: false, expected_version: 1 });
    expect(api.execute.mock.calls[1][1]).toMatchObject({ automatic_promotion: true, expected_version: 1 });
    expect(component.lastProposal?.state).toBe('promoted');
  });

  it('polls a delegated proposal job to a candidate without human interaction', async () => {
    vi.useFakeTimers();
    const candidate = result('candidate_ready');
    const api = {
      execute: vi.fn().mockReturnValue(of({
        schema: 'ananta.spreadsheet-execution-job.v1',
        job_id: 'spreadsheet-job-one',
        proposal_id: 'proposal-one',
        document_id: 'document-one',
        proposal_digest: 'a'.repeat(64),
        status: 'leased',
        queue_position: null,
        automatic_decision: true,
        human_intervention_required: false,
      })),
      proposalJob: vi.fn().mockReturnValue(of({
        schema: 'ananta.spreadsheet-execution-job.v1',
        job_id: 'spreadsheet-job-one',
        proposal_id: 'proposal-one',
        document_id: 'document-one',
        proposal_digest: 'a'.repeat(64),
        status: 'completed',
        queue_position: null,
        result: candidate,
        automatic_decision: true,
        human_intervention_required: false,
      })),
    };
    TestBed.configureTestingModule({
      providers: [
        { provide: SpreadsheetStudioApiService, useValue: api },
        { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub.test' }] } },
      ],
    });
    const component = TestBed.runInInjectionContext(() => new SpreadsheetStudioPageComponent());
    component.selected = document();
    component.documents = [document()];
    component.viewerSynchronized = true;
    component.automaticApply = false;

    component.execute();
    await vi.runOnlyPendingTimersAsync();

    expect(api.proposalJob).toHaveBeenCalledWith('http://hub.test', 'spreadsheet-job-one');
    expect(component.lastProposal?.state).toBe('candidate_ready');
    expect(component.proposalJob).toBeNull();
  });
});
