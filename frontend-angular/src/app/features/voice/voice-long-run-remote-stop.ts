import { firstValueFrom } from 'rxjs';

import type { VoiceApiService } from './voice-api.service';
import {
  MAX_STOP_CORRECTION_ATTEMPTS,
  STOP_CORRECTION_POLL_MILLISECONDS,
  delay,
  isSegmentsInFlight,
  terminalRun,
} from './voice-long-run.policy';
import type { VoiceLongRunResponse } from './voice.models';

/** Controller state the remote stop reads or publishes to. */
export interface VoiceLongRunRemoteStopPort {
  hubUrl(): string;
  runId(): string | null;
  lastLocalSequence(): number;
  ensureOperation(generation: number): void;
  publishResponse(response: VoiceLongRunResponse): void;
  refreshWhileStopping(generation: number): Promise<void>;
}

/**
 * Idempotent Hub stop of the active long run. While the Hub still reports
 * segments in flight or a non-terminal run, the stop is retried after a
 * correction refresh, bounded by MAX_STOP_CORRECTION_ATTEMPTS.
 */
export class VoiceLongRunRemoteStop {
  constructor(
    private readonly api: Pick<VoiceApiService, 'stopLongRun'>,
    private readonly port: VoiceLongRunRemoteStopPort,
  ) {}

  async stop(reason: string): Promise<VoiceLongRunResponse> {
    const runId = this.port.runId();
    if (!runId) throw new Error('voice.long_run.not_active');
    return firstValueFrom(this.api.stopLongRun(
      this.port.hubUrl(),
      runId,
      { last_sequence: this.port.lastLocalSequence(), reason },
      `voice-ui:long-run-stop:${runId}`,
    ));
  }

  async stopAfterCorrections(
    reason: string,
    generation: number,
  ): Promise<VoiceLongRunResponse> {
    let lastInFlightError: unknown = null;
    for (let attempt = 0; attempt < MAX_STOP_CORRECTION_ATTEMPTS; attempt += 1) {
      try {
        const response = await this.stop(reason);
        this.port.ensureOperation(generation);
        if (terminalRun(response.run)) return response;
        this.port.publishResponse(response);
      } catch (error) {
        this.port.ensureOperation(generation);
        if (!isSegmentsInFlight(error)) throw error;
        lastInFlightError = error;
      }
      await this.port.refreshWhileStopping(generation);
      if (attempt < MAX_STOP_CORRECTION_ATTEMPTS - 1) {
        await delay(STOP_CORRECTION_POLL_MILLISECONDS);
        this.port.ensureOperation(generation);
      }
    }
    throw lastInFlightError || new Error('voice.long_run.correction_drain_timeout');
  }
}
