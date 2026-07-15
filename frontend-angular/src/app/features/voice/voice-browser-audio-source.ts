export type VoiceCaptureSource = 'microphone' | 'system_audio';

export interface BrowserAudioStreamLease {
  readonly source: VoiceCaptureSource;
  readonly audioStream: MediaStream;
  readonly ended: boolean;
  onEnded(handler: () => void): () => void;
  release(): void;
}

type CaptureMediaDevices = Pick<MediaDevices, 'getUserMedia' | 'getDisplayMedia'>;
type AudioStreamFactory = (tracks: MediaStreamTrack[]) => MediaStream;

export function browserCaptureSourceSupported(
  source: VoiceCaptureSource,
  mediaDevices: Partial<CaptureMediaDevices> | undefined = globalThis.navigator?.mediaDevices,
): boolean {
  if (source === 'system_audio') return typeof mediaDevices?.getDisplayMedia === 'function';
  return typeof mediaDevices?.getUserMedia === 'function';
}

/**
 * Acquire one explicitly consented browser source while retaining ownership of
 * every underlying track. Display video is required by getDisplayMedia but is
 * deliberately excluded from audioStream, so it can never reach MediaRecorder
 * or the Hub.
 */
export async function acquireBrowserAudioStream(
  source: VoiceCaptureSource,
  mediaDevices: CaptureMediaDevices = globalThis.navigator.mediaDevices,
  streamFactory: AudioStreamFactory = (tracks) => new MediaStream(tracks),
): Promise<BrowserAudioStreamLease> {
  if (!browserCaptureSourceSupported(source, mediaDevices)) {
    throw new Error(source === 'system_audio'
      ? 'voice.capture.system_audio_not_supported'
      : 'voice.capture.browser_not_supported');
  }

  if (source === 'microphone') {
    const stream = await mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    return new OwnedBrowserAudioStreamLease(source, stream, stream);
  }

  const displayStream = await mediaDevices.getDisplayMedia({
    video: true,
    audio: true,
    // Chromium uses this hint where supported; other browsers safely ignore it.
    systemAudio: 'include',
  } as DisplayMediaStreamOptions);
  const audioTracks = displayStream.getAudioTracks();
  if (!audioTracks.length) {
    displayStream.getTracks().forEach((track) => track.stop());
    throw new Error('voice.capture.system_audio_not_shared');
  }
  try {
    return new OwnedBrowserAudioStreamLease(
      source,
      streamFactory(audioTracks),
      displayStream,
    );
  } catch (error) {
    displayStream.getTracks().forEach((track) => track.stop());
    throw error;
  }
}

class OwnedBrowserAudioStreamLease implements BrowserAudioStreamLease {
  private readonly endedHandlers = new Set<() => void>();
  private readonly trackEnded = () => this.notifyEnded();
  private readonly observedTracks: readonly MediaStreamTrack[];
  private released = false;
  private endedNotified = false;

  constructor(
    readonly source: VoiceCaptureSource,
    readonly audioStream: MediaStream,
    ownerStream: MediaStream,
  ) {
    this.observedTracks = [...new Set([
      ...this.audioStream.getTracks(),
      ...ownerStream.getTracks(),
    ])];
    this.observedTracks.forEach((track) => {
      track.addEventListener('ended', this.trackEnded);
    });
  }

  get ended(): boolean {
    return this.endedNotified
      || this.observedTracks.some((track) => track.readyState === 'ended');
  }

  onEnded(handler: () => void): () => void {
    if (this.released) return () => undefined;
    if (this.ended) {
      handler();
      return () => undefined;
    }
    this.endedHandlers.add(handler);
    return () => this.endedHandlers.delete(handler);
  }

  release(): void {
    if (this.released) return;
    this.released = true;
    this.observedTracks.forEach((track) => {
      track.removeEventListener('ended', this.trackEnded);
    });
    this.observedTracks.forEach((track) => track.stop());
    this.endedHandlers.clear();
  }

  private notifyEnded(): void {
    if (this.released || this.endedNotified) return;
    this.endedNotified = true;
    [...this.endedHandlers].forEach((handler) => handler());
  }
}
