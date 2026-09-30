import { normalizedRunVersion } from './voice-long-run.policy';
import { VoiceLongRunResponse, VoiceLongRunSegmentUploadResponse } from './voice.models';

/**
 * Merged view of segment gaps: locally lost segments (spool eviction,
 * failed uploads) and Hub-reported gaps, ordered by the Hub run version so a
 * stale response can never resurrect an old gap set.
 */
export class VoiceLongRunGapProjection {
  private readonly localGaps = new Set<number>();
  private hubGaps = new Set<number>();
  private latestRunVersion: number | null = null;

  constructor(private readonly emit: (sequences: readonly number[]) => void) {}

  get localSequences(): number[] {
    return [...this.localGaps].sort((left, right) => left - right);
  }

  reset(notify: boolean): void {
    this.localGaps.clear();
    this.hubGaps.clear();
    this.latestRunVersion = null;
    if (notify) this.emit([]);
  }

  /** Records a local gap; false when the sequence was already known. */
  reportLocal(sequence: number): boolean {
    if (this.localGaps.has(sequence)) return false;
    this.localGaps.add(sequence);
    return true;
  }

  clearLocal(sequence: number): void {
    this.localGaps.delete(sequence);
  }

  /**
   * Applies a run response of the current run. Returns false for a stale
   * projection (older run version); the merged set is emitted either way.
   * `accepted` runs before the emission, once the response is known current.
   */
  applyHubResponse(response: VoiceLongRunResponse, accepted: () => void): boolean {
    const completed = [
      ...(response.segments || []),
      ...((response as VoiceLongRunSegmentUploadResponse).segment
        ? [(response as VoiceLongRunSegmentUploadResponse).segment]
        : []),
    ].filter((segment) => segment.status === 'completed');
    for (const segment of completed) this.localGaps.delete(segment.sequence);

    const version = normalizedRunVersion(response.run.version);
    if ((version == null && this.latestRunVersion != null)
      || (version != null && this.latestRunVersion != null && version < this.latestRunVersion)) {
      this.emitMerged();
      return false;
    }
    if (version != null) this.latestRunVersion = version;
    accepted();
    const completedSequences = new Set(completed.map((segment) => segment.sequence));
    this.hubGaps = new Set((response.gaps || []).filter((sequence) => (
      Number.isInteger(sequence) && sequence >= 0 && !completedSequences.has(sequence)
    )));
    this.emitMerged();
    return true;
  }

  emitMerged(): void {
    this.emit(
      [...new Set([...this.hubGaps, ...this.localGaps])]
        .sort((left, right) => left - right),
    );
  }
}
