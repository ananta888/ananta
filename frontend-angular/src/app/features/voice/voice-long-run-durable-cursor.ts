import { VoiceLongRunRecoveryMetadata } from './voice-long-run-recovery';
import { VoiceLongRunCursor } from './voice-long-run.policy';

/**
 * Timeline positions of a long run as far as they are captured, cut into
 * complete segments and durably spooled. They feed the recovery descriptor
 * so a reload can resume without re-sending or silently skipping audio.
 */
export class VoiceLongRunDurableCursor {
  latestTimelineMilliseconds = 0;
  private completedTimelineMilliseconds = 0;
  private durableNextSequence = 0;
  private durableTimelineMilliseconds = 0;

  adopt(cursor: VoiceLongRunCursor): void {
    this.latestTimelineMilliseconds = cursor.timelineMilliseconds;
    this.durableNextSequence = cursor.durableNextSequence;
    this.durableTimelineMilliseconds = cursor.durableTimelineMilliseconds;
    this.completedTimelineMilliseconds = cursor.completedTimelineMilliseconds;
  }

  segmentCompleted(endedAtMs: number): void {
    this.completedTimelineMilliseconds = Math.max(this.completedTimelineMilliseconds, endedAtMs);
  }

  segmentDurable(sequence: number, endedAtMs: number): void {
    this.durableNextSequence = Math.max(this.durableNextSequence, sequence + 1);
    this.durableTimelineMilliseconds = Math.max(this.durableTimelineMilliseconds, endedAtMs);
  }

  reset(): void {
    this.latestTimelineMilliseconds = 0;
    this.completedTimelineMilliseconds = 0;
    this.durableNextSequence = 0;
    this.durableTimelineMilliseconds = 0;
  }

  descriptorFields(): Pick<
    VoiceLongRunRecoveryMetadata,
    'completedTimelineMilliseconds' | 'durableNextSequence' | 'durableTimelineMilliseconds'
  > {
    return {
      completedTimelineMilliseconds: this.completedTimelineMilliseconds,
      durableNextSequence: this.durableNextSequence,
      durableTimelineMilliseconds: this.durableTimelineMilliseconds,
    };
  }
}
