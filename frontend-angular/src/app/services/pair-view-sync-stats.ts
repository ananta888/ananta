import { Observable, Subject } from 'rxjs';

/** Counters of one PairViewSyncService instance, emitted after every change. */
export interface PairSyncStats {
  snapshotsSent: number;
  deltasSent: number;
  cursorsSent: number;
  cursorsReceived: number;
  appliesAccepted: number;
  appliesRejected: number;
  snapshotRequestsSent: number;
  snapshotRequestsReceived: number;
  controlGranted: number;
  controlDenied: number;
  controlRevoked: number;
}

export type PairSyncStatsCounter = keyof PairSyncStats;

/**
 * Owns the pair view-sync counters and their change stream. Every increment
 * emits one immutable copy, matching the observable contract of `stats$`.
 */
export class PairSyncStatsRecorder {
  /** Live counter object (read-only for callers; mutated only via increment). */
  readonly counters: PairSyncStats = {
    snapshotsSent: 0, deltasSent: 0, cursorsSent: 0, cursorsReceived: 0,
    appliesAccepted: 0, appliesRejected: 0,
    snapshotRequestsSent: 0, snapshotRequestsReceived: 0,
    controlGranted: 0, controlDenied: 0, controlRevoked: 0,
  };
  private readonly changes = new Subject<PairSyncStats>();
  readonly stats$: Observable<PairSyncStats> = this.changes.asObservable();

  increment(counter: PairSyncStatsCounter): void {
    this.counters[counter] = (this.counters[counter] ?? 0) + 1;
    this.changes.next({ ...this.counters });
  }
}
