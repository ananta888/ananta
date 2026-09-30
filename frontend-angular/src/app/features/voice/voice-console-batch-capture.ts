import type { VoiceBatchRecordingPort, VoiceCaptureSource } from './voice-audio-capture';

/** Batch recording view fields of the console that the capture updates. */
export interface VoiceConsoleBatchView {
  batchRecording: boolean;
  batchBusy: boolean;
  batchAudio: Blob | File | null;
  batchFileName: string;
  batchResult: unknown;
  successMessage: string;
}

/** Console services the batch capture reports through. */
export interface VoiceConsoleBatchHost {
  readonly view: VoiceConsoleBatchView;
  destroyed(): boolean;
  markForCheck(): void;
  clearMessages(): void;
  fail(error: unknown, cleanup?: () => void): void;
}

/**
 * Local batch recording of the voice console: one generation-fenced
 * recording at a time, ended by the user, the platform (source ended,
 * safety limit, notification stop) or a capture error. Audio stays local
 * until the console explicitly transcribes it through the Hub.
 */
export class VoiceConsoleBatchCapture {
  private generation = 0;
  private operation: { generation: number; ending: boolean } | null = null;

  constructor(
    private readonly recorder: VoiceBatchRecordingPort,
    private readonly host: VoiceConsoleBatchHost,
  ) {}

  /** Invalidates every in-flight recording step (console destroyed). */
  invalidate(): void {
    this.generation += 1;
  }

  async start(captureSource: VoiceCaptureSource): Promise<void> {
    const view = this.host.view;
    const generation = ++this.generation;
    const operation = { generation, ending: false };
    this.operation = operation;
    view.batchBusy = true;
    view.batchResult = null;
    view.batchAudio = null;
    view.batchFileName = '';
    this.host.clearMessages();
    try {
      await this.recorder.start(captureSource, {
        ended: (reason) => this.onCaptureEnded(generation, reason),
        error: () => this.onCaptureEnded(generation, 'capture_error'),
      });
      if (!this.isCurrent(generation)) throw new Error('voice.capture.cancelled');
      if (operation.ending) return;
      view.batchRecording = true;
      view.batchBusy = false;
      view.successMessage = captureSource === 'system_audio'
        ? 'Systemaudio-Aufnahme läuft lokal. Erst mit „Über Hub transkribieren“ wird Audio an den Hub gesendet.'
        : 'Mikrofon-Aufnahme läuft lokal. Erst mit „Über Hub transkribieren“ wird Audio an den Hub gesendet.';
      this.host.markForCheck();
    } catch (error) {
      if (this.isCurrent(generation) && !operation.ending) {
        this.operation = null;
        this.host.fail(error, () => { view.batchBusy = false; });
      }
    }
  }

  async stop(): Promise<void> {
    if (!this.host.view.batchRecording) return;
    const operation = this.operation;
    if (!operation || operation.ending) return;
    operation.ending = true;
    await this.finish(operation.generation, false);
  }

  private onCaptureEnded(generation: number, reason?: string): void {
    const operation = this.operation;
    if (!this.isCurrent(generation) || !operation || operation.ending) return;
    operation.ending = true;
    void this.finish(generation, true, reason);
  }

  private async finish(
    generation: number,
    endedAutomatically: boolean,
    stopReason?: string,
  ): Promise<void> {
    const view = this.host.view;
    if (!this.isCurrent(generation)) return;
    view.batchBusy = true;
    view.batchRecording = false;
    this.host.markForCheck();
    try {
      const audio = await this.recorder.stop();
      if (!this.isCurrent(generation)) return;
      view.batchAudio = audio;
      view.batchFileName = audio.type === 'audio/wav' ? 'voice-recording.wav'
        : audio.type.includes('mp4') ? 'voice-recording.m4a'
        : 'voice-recording.webm';
      view.batchBusy = false;
      this.operation = null;
      view.successMessage = endedAutomatically
        ? batchAutomaticStopMessage(stopReason)
        : 'Aufnahme beendet. Sie kann jetzt über den Hub transkribiert werden.';
      this.host.markForCheck();
    } catch (error) {
      if (!this.isCurrent(generation)) return;
      this.operation = null;
      this.host.fail(error, () => { view.batchBusy = false; });
    }
  }

  private isCurrent(generation: number): boolean {
    return !this.host.destroyed() && generation === this.generation;
  }
}

function batchAutomaticStopMessage(reason?: string): string {
  if (reason === 'safety_limit') {
    return 'Die maximale Aufnahmedauer wurde erreicht. Die lokale Aufnahme kann jetzt über den Hub transkribiert werden.';
  }
  if (reason === 'notification_stop') {
    return 'Die Aufnahme wurde über Android beendet. Sie kann jetzt über den Hub transkribiert werden.';
  }
  if (reason === 'source_ended' || reason === 'projection_revoked') {
    return 'Die Audiofreigabe wurde beendet. Die lokale Aufnahme kann jetzt über den Hub transkribiert werden.';
  }
  return 'Die Aufnahme wurde automatisch beendet. Sie kann jetzt über den Hub transkribiert werden.';
}
