import { VoiceLongRunRecoveryPort } from './voice-long-run-recovery';
import { captureDeadlineExpired, runExpired } from './voice-long-run.policy';
import { VoiceLongRunResponse } from './voice.models';

const INSPECTION_FRESHNESS_MILLISECONDS = 30_000;

/**
 * The most recent Hub status check of the locally recoverable run and the
 * recovery modes it permits (re-consent to capture, drain-only, finalizing).
 * A check older than 30 seconds no longer authorizes a capture consent.
 */
export class VoiceLongRunRecoveryInspection {
  private inspected: { response: VoiceLongRunResponse; inspectedAt: number } | null = null;

  constructor(private readonly recovery: VoiceLongRunRecoveryPort) {}

  get response(): VoiceLongRunResponse | null { return this.inspected?.response ?? null; }

  record(response: VoiceLongRunResponse): void {
    this.inspected = { response, inspectedAt: Date.now() };
  }

  forget(): void {
    this.inspected = null;
  }

  readyForConsent(): boolean {
    const descriptor = this.recovery.load();
    const inspected = this.inspected;
    return Boolean(
      descriptor?.runId
      && inspected?.response.run.id === descriptor.runId
      && inspected.response.run.status === 'active'
      && !captureDeadlineExpired(inspected.response.run)
      && Date.now() - inspected.inspectedAt <= INSPECTION_FRESHNESS_MILLISECONDS,
    );
  }

  drainOnly(): boolean {
    const descriptor = this.recovery.load();
    const inspected = this.inspected;
    return Boolean(
      descriptor?.runId
      && inspected?.response.run.id === descriptor.runId
      && inspected.response.run.status === 'active'
      && captureDeadlineExpired(inspected.response.run)
      && !runExpired(inspected.response.run)
      && Date.now() - inspected.inspectedAt <= INSPECTION_FRESHNESS_MILLISECONDS,
    );
  }

  finalizing(): boolean {
    const descriptor = this.recovery.load();
    return Boolean(
      descriptor?.runId
      && this.inspected?.response.run.id === descriptor.runId
      && this.inspected.response.run.status === 'finalizing',
    );
  }
}
