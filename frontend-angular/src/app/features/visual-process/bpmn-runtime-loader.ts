import { Observable, catchError, forkJoin, map, of, switchMap, take, timeout } from 'rxjs';
import type { BpmnEdgeTrace, WorkflowStatus } from './visual-process-api.service';
import { BpmnRuntimeView, bpmnRuntimeView, sameBpmnRuntimeSnapshot } from './bpmn-runtime-view';

/** Only the three authenticated history reads needed to reconstruct a BPMN run. */
export interface BpmnRuntimeReadPort {
  getWorkflowStatus(id: string): Observable<WorkflowStatus>;
  getWorkflowEvents(id: string): Observable<{ events: Record<string, unknown>[] }>;
  getBpmnEdgeTrace(id: string, runId: string): Observable<BpmnEdgeTrace>;
}

export interface BpmnRuntimeReadResult {
  identityMatches: boolean;
  consistent: boolean;
  historyAvailable: boolean;
  view: BpmnRuntimeView;
}

const bounded = <T>(source: Observable<T>): Observable<T> => source.pipe(take(1), timeout(15000));

/** No browser state is authoritative: fence history with a second Hub status read. */
export function loadBpmnRuntime(port: BpmnRuntimeReadPort, id: string, current: () => boolean): Observable<BpmnRuntimeReadResult> {
  return bounded(port.getWorkflowStatus(id)).pipe(switchMap(status => {
    if (!current() || status.workflow_id !== id || typeof status['run_id'] !== 'string' || !status['run_id']) {
      return of({ identityMatches: status.workflow_id === id, consistent: true,
        historyAvailable: false, view: bpmnRuntimeView(status) });
    }
    return forkJoin({
      events: bounded(port.getWorkflowEvents(id)).pipe(catchError(() => of(null))),
      trace: bounded(port.getBpmnEdgeTrace(id, status['run_id'])).pipe(catchError(() => of(null))),
    }).pipe(switchMap(history => bounded(port.getWorkflowStatus(id)).pipe(map(latest => ({
      identityMatches: latest.workflow_id === id,
      consistent: sameBpmnRuntimeSnapshot(status, latest),
      historyAvailable: history.events !== null,
      view: bpmnRuntimeView(status, history.events?.events || [], history.trace),
    })))));
  }));
}
