import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of, toArray } from 'rxjs';

import { SpreadsheetStudioApiService } from './spreadsheet-studio-api.service';
import { SpreadsheetProposalWorkflowService } from './spreadsheet-proposal-workflow.service';
import { SpreadsheetProposalJob, SpreadsheetProposalResult } from './spreadsheet-studio.models';

describe('SpreadsheetProposalWorkflowService', () => {
  afterEach(() => vi.useRealTimers());

  it('follows a delegated Hub job to its automatic terminal proposal result', async () => {
    vi.useFakeTimers();
    const result = {
      proposal_id: 'proposal-one', state: 'candidate_ready',
    } as SpreadsheetProposalResult;
    const initial = {
      job_id: 'job-one', status: 'leased',
    } as SpreadsheetProposalJob;
    const complete = {
      ...initial, status: 'completed', result,
    } as SpreadsheetProposalJob;
    const api = {
      execute: vi.fn().mockReturnValue(of(initial)),
      proposalJob: vi.fn().mockReturnValue(of(complete)),
    };
    TestBed.configureTestingModule({
      providers: [
        SpreadsheetProposalWorkflowService,
        { provide: SpreadsheetStudioApiService, useValue: api },
      ],
    });
    const workflow = TestBed.inject(SpreadsheetProposalWorkflowService);

    const eventsPromise = firstValueFrom(workflow.execute('http://hub.test', {}).pipe(toArray()));
    await vi.runOnlyPendingTimersAsync();
    const events = await eventsPromise;

    expect(events.map(event => event.kind)).toEqual(['job', 'job', 'result']);
    expect(events.at(-1)).toEqual({ kind: 'result', result });
  });

  it('returns synchronous mock-mode results without starting job polling', async () => {
    const result = { proposal_id: 'proposal-one', state: 'promoted' } as SpreadsheetProposalResult;
    const api = { execute: vi.fn().mockReturnValue(of(result)), proposalJob: vi.fn() };
    TestBed.configureTestingModule({
      providers: [
        SpreadsheetProposalWorkflowService,
        { provide: SpreadsheetStudioApiService, useValue: api },
      ],
    });

    const events = await firstValueFrom(TestBed.inject(SpreadsheetProposalWorkflowService).execute('http://hub.test', {}));

    expect(events).toEqual({ kind: 'result', result });
    expect(api.proposalJob).not.toHaveBeenCalled();
  });
});
