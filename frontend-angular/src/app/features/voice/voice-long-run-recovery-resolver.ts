import { firstValueFrom } from 'rxjs';

import type { VoiceApiService } from './voice-api.service';
import type { VoiceLongRunRecoveryPort } from './voice-long-run-recovery';
import type { VoiceLongRunRecoveryInspection } from './voice-long-run-recovery-inspection';
import type { VoiceLongRunSpoolPort } from './voice-long-run-spool';
import { isNotFound, runExpired, terminalRun } from './voice-long-run.policy';
import type { VoiceLongRunResponse } from './voice.models';

/** Controller state the recovery resolver reads or updates. */
export interface VoiceLongRunRecoveryResolverPort {
  generation(): number;
  ensureOperation(generation: number): void;
  initializeSecureStorage(): Promise<void>;
  /** Keeps a profile deletion relevant while the descriptor is being resolved. */
  setPendingProfileId(profileId: string): void;
  reportError(error: unknown): void;
}

/**
 * Resolves the locally persisted recovery descriptor without capturing:
 * inspection against the Hub (forgetting ended or unknown runs) and the
 * user's discard, which is locally authoritative even when the Hub is
 * unreachable. Resume and drain stay in VoiceLongRunController because they
 * own the capture runtime.
 */
export class VoiceLongRunRecoveryResolver {
  constructor(
    private readonly api: Pick<VoiceApiService, 'getLongRun' | 'stopLongRun'>,
    private readonly spool: Pick<VoiceLongRunSpoolPort, 'clearRun'>,
    private readonly recovery: VoiceLongRunRecoveryPort,
    private readonly inspection: VoiceLongRunRecoveryInspection,
    private readonly port: VoiceLongRunRecoveryResolverPort,
  ) {}

  async inspect(): Promise<VoiceLongRunResponse | null> {
    const generation = this.port.generation();
    const descriptor = this.recovery.load();
    if (!descriptor?.runId) return null;
    await this.port.initializeSecureStorage();
    this.port.ensureOperation(generation);
    let response: VoiceLongRunResponse;
    try {
      response = await firstValueFrom(this.api.getLongRun(
        descriptor.hubUrl,
        descriptor.runId,
        { includeText: false },
      ));
      this.port.ensureOperation(generation);
    } catch (error) {
      this.port.ensureOperation(generation);
      if (!isNotFound(error)) throw error;
      await this.spool.clearRun(descriptor.runId);
      this.port.ensureOperation(generation);
      this.recovery.clear(descriptor.runId);
      this.inspection.forget();
      return null;
    }
    if (terminalRun(response.run) || runExpired(response.run)) {
      await this.spool.clearRun(descriptor.runId);
      this.port.ensureOperation(generation);
      this.recovery.clear(descriptor.runId);
      this.inspection.forget();
      return response;
    }
    this.inspection.record(response);
    return response;
  }

  async discard(): Promise<boolean> {
    const generation = this.port.generation();
    const descriptor = this.recovery.load();
    if (!descriptor) return true;
    this.port.setPendingProfileId(descriptor.request.profile_id);
    let remoteFailure: unknown = null;
    let hubConfirmedEnded = !descriptor.runId;
    if (descriptor.runId) {
      try {
        const snapshot = await firstValueFrom(this.api.getLongRun(
          descriptor.hubUrl,
          descriptor.runId,
          { includeText: false },
        ));
        this.port.ensureOperation(generation);
        if (snapshot.run.status === 'active') {
          await firstValueFrom(this.api.stopLongRun(
            descriptor.hubUrl,
            descriptor.runId,
            { last_sequence: descriptor.nextSequence - 1, reason: 'user_discard' },
            `voice-ui:long-run-stop:${descriptor.runId}`,
          ));
          this.port.ensureOperation(generation);
          hubConfirmedEnded = true;
        } else if (terminalRun(snapshot.run) || runExpired(snapshot.run)) {
          hubConfirmedEnded = true;
        }
      } catch (error) {
        this.port.ensureOperation(generation);
        if (isNotFound(error)) hubConfirmedEnded = true;
        else remoteFailure = error;
      }
      await this.spool.clearRun(descriptor.runId);
      this.port.ensureOperation(generation);
    }
    // Local discard is authoritative even when the Hub is offline or already
    // returned 404 (for example after a privacy deletion).
    this.recovery.clear();
    this.port.setPendingProfileId('');
    if (remoteFailure) this.port.reportError(remoteFailure);
    return hubConfirmedEnded;
  }
}
