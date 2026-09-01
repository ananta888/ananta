import { Injectable, inject } from '@angular/core';
import {
  Observable,
  concat,
  concatMap,
  exhaustMap,
  of,
  retry,
  switchMap,
  takeWhile,
  throwError,
  timer,
} from 'rxjs';

import { SpreadsheetStudioApiService } from './spreadsheet-studio-api.service';
import { SpreadsheetProposalJob, SpreadsheetProposalResult } from './spreadsheet-studio.models';

export type SpreadsheetProposalWorkflowEvent =
  | { kind: 'job'; job: SpreadsheetProposalJob }
  | { kind: 'result'; result: SpreadsheetProposalResult };

@Injectable({ providedIn: 'root' })
export class SpreadsheetProposalWorkflowService {
  private readonly api = inject(SpreadsheetStudioApiService);

  execute(
    hubUrl: string,
    proposal: Record<string, unknown>,
  ): Observable<SpreadsheetProposalWorkflowEvent> {
    return this.api.execute(hubUrl, proposal).pipe(
      switchMap(response => 'job_id' in response
        ? concat(of({ kind: 'job' as const, job: response }), this.followJob(hubUrl, response))
        : of({ kind: 'result' as const, result: response })),
    );
  }

  private followJob(
    hubUrl: string,
    initial: SpreadsheetProposalJob,
  ): Observable<SpreadsheetProposalWorkflowEvent> {
    if (this.terminal(initial)) return this.jobEvents(initial);
    return timer(0, 1_000).pipe(
      exhaustMap(() => this.api.proposalJob(hubUrl, initial.job_id)),
      retry({
        delay: (_error, retryCount) => timer(Math.min(1_000 * (2 ** Math.min(retryCount - 1, 5)), 30_000)),
      }),
      takeWhile(job => !this.terminal(job), true),
      concatMap(job => this.jobEvents(job)),
    );
  }

  private jobEvents(job: SpreadsheetProposalJob): Observable<SpreadsheetProposalWorkflowEvent> {
    const status = of<SpreadsheetProposalWorkflowEvent>({ kind: 'job', job });
    if (!this.terminal(job)) return status;
    const result = job.result;
    if (job.status !== 'completed' || !result || !('state' in result)) {
      const reason = String(result && 'reason_code' in result ? result.reason_code : 'spreadsheet_proposal_job_failed');
      return concat(status, throwError(() => new Error(reason)));
    }
    return concat(status, of<SpreadsheetProposalWorkflowEvent>({ kind: 'result', result }));
  }

  private terminal(job: SpreadsheetProposalJob): boolean {
    return job.status === 'completed' || job.status === 'failed';
  }
}
