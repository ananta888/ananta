import { firstValueFrom } from 'rxjs';

import type { VoiceApiService } from './voice-api.service';
import { HEARTBEAT_MILLISECONDS } from './voice-long-run.policy';
import type { VoiceLongRunResponse, VoiceLongRunState } from './voice.models';

/** Controller state a heartbeat reads and the effects it triggers. */
export interface VoiceLongRunHeartbeatPort {
  hubUrl(): string;
  run(): VoiceLongRunState | null;
  generation(): number;
  lastLocalSequence(): number;
  localGaps(): number[];
  /** Timeline position to persist in the recovery descriptor after a beat. */
  timelineMilliseconds(): number;
  saveRecovery(nextSequence: number, timelineMilliseconds: number): void;
  /** Applies the Hub gap projection; true when the run projection changed. */
  acceptRunProjection(response: VoiceLongRunResponse): boolean;
  runUpdated(response: VoiceLongRunResponse): void;
  kickUploader(): void;
  connectionRetrying(): void;
}

/**
 * Text-free liveness beat of an active long run: reports the local cursor
 * and gaps, refreshes the recovery descriptor and the Hub gap projection,
 * and nudges the uploader. A beat of a superseded operation is ignored.
 */
export class VoiceLongRunHeartbeat {
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly api: Pick<VoiceApiService, 'heartbeatLongRun'>,
    private readonly port: VoiceLongRunHeartbeatPort,
  ) {}

  start(beat: () => void): void {
    this.clear();
    this.timer = setInterval(beat, HEARTBEAT_MILLISECONDS);
  }

  clear(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async send(): Promise<void> {
    const run = this.port.run();
    const runId = run?.id;
    if (!runId || run?.status !== 'active') return;
    const generation = this.port.generation();
    try {
      const response = await firstValueFrom(this.api.heartbeatLongRun(this.port.hubUrl(), runId, {
        client_time_ms: Date.now(),
        last_local_sequence: this.port.lastLocalSequence(),
        gaps: this.port.localGaps(),
      }));
      if (generation !== this.port.generation()) return;
      this.port.saveRecovery(this.port.lastLocalSequence() + 1, this.port.timelineMilliseconds());
      // Heartbeats intentionally omit transcript text. They may observe a
      // newer Hub revision, but must never advance the content cursor or
      // replace visible text before the text-bearing revision delta arrives.
      if (this.port.acceptRunProjection(response)) this.port.runUpdated(response);
      this.port.kickUploader();
    } catch {
      if (generation !== this.port.generation()) return;
      this.port.connectionRetrying();
    }
  }
}
