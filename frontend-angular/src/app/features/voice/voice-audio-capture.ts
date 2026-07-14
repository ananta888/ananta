import { InjectionToken } from '@angular/core';
import { Capacitor, PluginListenerHandle, registerPlugin } from '@capacitor/core';

export const VOICE_PCM_MEDIA_TYPE = 'audio/pcm;rate=16000;channels=1';
export const VOICE_PCM_SAMPLE_RATE = 16_000;

export type VoicePcmChunkHandler = (chunk: ArrayBuffer) => void;

/** Platform seam: Capacitor can provide a native AudioRecord implementation. */
export interface VoiceAudioCapturePort {
  readonly supported: boolean;
  readonly active: boolean;
  start(onChunk: VoicePcmChunkHandler, onError?: (error: unknown) => void): Promise<void>;
  stop(): Promise<void>;
}

/** Platform seam for a complete recording sent to the canonical Hub batch API. */
export interface VoiceBatchRecordingPort {
  readonly supported: boolean;
  readonly active: boolean;
  start(): Promise<void>;
  stop(): Promise<Blob>;
  cancel(): Promise<void>;
}

/**
 * Deterministically converts mono Web-Audio samples to signed little-endian
 * PCM16. It is deliberately exported so browser and native adapters can be
 * verified against the exact Hub media contract.
 */
export function float32MonoToPcm16(
  samples: Float32Array,
  sourceSampleRate: number,
  targetSampleRate = VOICE_PCM_SAMPLE_RATE,
): ArrayBuffer {
  if (!Number.isFinite(sourceSampleRate) || sourceSampleRate <= 0) {
    throw new Error('voice.capture.invalid_source_sample_rate');
  }
  if (!Number.isFinite(targetSampleRate) || targetSampleRate <= 0 || targetSampleRate > sourceSampleRate) {
    throw new Error('voice.capture.invalid_target_sample_rate');
  }
  if (!samples.length) return new ArrayBuffer(0);

  const ratio = sourceSampleRate / targetSampleRate;
  const outputLength = Math.max(1, Math.floor(samples.length / ratio));
  const output = new ArrayBuffer(outputLength * Int16Array.BYTES_PER_ELEMENT);
  const view = new DataView(output);

  for (let outputIndex = 0; outputIndex < outputLength; outputIndex += 1) {
    const start = Math.floor(outputIndex * ratio);
    const end = Math.max(start + 1, Math.min(samples.length, Math.floor((outputIndex + 1) * ratio)));
    let sum = 0;
    for (let inputIndex = start; inputIndex < end; inputIndex += 1) sum += samples[inputIndex];
    const value = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    const pcm = value < 0 ? Math.round(value * 0x8000) : Math.round(value * 0x7fff);
    view.setInt16(outputIndex * 2, pcm, true);
  }
  return output;
}

export class Pcm16ChunkAccumulator {
  private pending = new Uint8Array(0);

  constructor(private readonly targetBytes = VOICE_PCM_SAMPLE_RATE) {
    if (!Number.isInteger(targetBytes) || targetBytes <= 0 || targetBytes % 2 !== 0) {
      throw new Error('voice.capture.invalid_pcm_chunk_size');
    }
  }

  push(chunk: ArrayBuffer, onChunk: VoicePcmChunkHandler): void {
    if (!chunk.byteLength) return;
    if (chunk.byteLength % 2 !== 0) throw new Error('voice.capture.unaligned_pcm_chunk');
    const next = new Uint8Array(this.pending.byteLength + chunk.byteLength);
    next.set(this.pending, 0);
    next.set(new Uint8Array(chunk), this.pending.byteLength);
    this.pending = next;
    while (this.pending.byteLength >= this.targetBytes) {
      const ready = this.pending.slice(0, this.targetBytes);
      this.pending = this.pending.slice(this.targetBytes);
      onChunk(ready.buffer);
    }
  }

  flush(onChunk: VoicePcmChunkHandler): void {
    if (this.pending.byteLength) onChunk(this.pending.slice().buffer);
    this.pending = new Uint8Array(0);
  }
}

export class BrowserVoiceAudioCaptureAdapter implements VoiceAudioCapturePort {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private sink: GainNode | null = null;
  private accumulator = new Pcm16ChunkAccumulator();
  private onChunk: VoicePcmChunkHandler | null = null;
  private onError: ((error: unknown) => void) | null = null;

  get supported(): boolean {
    return Boolean(globalThis.navigator?.mediaDevices?.getUserMedia && globalThis.AudioContext);
  }

  get active(): boolean {
    return this.stream !== null;
  }

  async start(onChunk: VoicePcmChunkHandler, onError?: (error: unknown) => void): Promise<void> {
    if (this.active) throw new Error('voice.capture.already_active');
    if (!this.supported) throw new Error('voice.capture.browser_not_supported');

    try {
      this.onChunk = onChunk;
      this.onError = onError || null;
      this.accumulator = new Pcm16ChunkAccumulator();
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      this.context = new AudioContext({ sampleRate: VOICE_PCM_SAMPLE_RATE });
      await this.context.resume();
      this.source = this.context.createMediaStreamSource(this.stream);
      this.sink = this.context.createGain();
      this.sink.gain.value = 0;
      this.sink.connect(this.context.destination);

      if (this.context.audioWorklet && typeof globalThis.AudioWorkletNode !== 'undefined') {
        try {
          await this.startWorklet(onChunk);
        } catch {
          this.startScriptProcessor(onChunk);
        }
      } else {
        this.startScriptProcessor(onChunk);
      }
    } catch (error) {
      await this.stop();
      throw error;
    }
  }

  async stop(): Promise<void> {
    this.onError = null;
    const context = this.context;
    this.worklet?.port.close();
    this.worklet?.disconnect();
    if (this.processor) this.processor.onaudioprocess = null;
    this.processor?.disconnect();
    this.source?.disconnect();
    this.sink?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.flushPcm();
    this.worklet = null;
    this.processor = null;
    this.source = null;
    this.sink = null;
    this.stream = null;
    this.context = null;
    this.onChunk = null;
    if (context && context.state !== 'closed') await context.close();
  }

  private async startWorklet(onChunk: VoicePcmChunkHandler): Promise<void> {
    if (!this.context || !this.source || !this.sink) return;
    const source = `
      class AnantaVoiceCaptureProcessor extends AudioWorkletProcessor {
        process(inputs) {
          const channel = inputs[0] && inputs[0][0];
          if (channel && channel.length) this.port.postMessage(channel.slice(0));
          return true;
        }
      }
      registerProcessor('ananta-voice-capture', AnantaVoiceCaptureProcessor);
    `;
    const moduleUrl = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
    try {
      await this.context.audioWorklet.addModule(moduleUrl);
    } finally {
      URL.revokeObjectURL(moduleUrl);
    }
    this.worklet = new AudioWorkletNode(this.context, 'ananta-voice-capture', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    this.worklet.port.onmessage = (event: MessageEvent<Float32Array>) => {
      try {
        const pcm = float32MonoToPcm16(event.data, this.context?.sampleRate || VOICE_PCM_SAMPLE_RATE);
        if (pcm.byteLength) this.bufferPcm(pcm, onChunk);
      } catch (error) {
        this.reportError(error);
      }
    };
    this.worklet.onprocessorerror = () => this.reportError(new Error('voice.capture.audio_worklet_failed'));
    this.source.connect(this.worklet);
    this.worklet.connect(this.sink);
  }

  private startScriptProcessor(onChunk: VoicePcmChunkHandler): void {
    if (!this.context || !this.source || !this.sink) return;
    this.processor = this.context.createScriptProcessor(4096, 1, 1);
    this.processor.onaudioprocess = (event: AudioProcessingEvent) => {
      try {
        const samples = event.inputBuffer.getChannelData(0);
        const pcm = float32MonoToPcm16(samples, event.inputBuffer.sampleRate);
        if (pcm.byteLength) this.bufferPcm(pcm, onChunk);
      } catch (error) {
        this.reportError(error);
      }
    };
    this.source.connect(this.processor);
    this.processor.connect(this.sink);
  }

  private bufferPcm(chunk: ArrayBuffer, onChunk: VoicePcmChunkHandler): void {
    // 500 ms at 16 kHz, mono, PCM16: bounded and inexpensive Hub requests.
    this.accumulator.push(chunk, onChunk);
  }

  private flushPcm(): void {
    if (this.onChunk) this.accumulator.flush(this.onChunk);
  }

  private reportError(error: unknown): void {
    const handler = this.onError;
    this.onError = null;
    handler?.(error);
  }
}

interface NativeVoiceChunkEvent {
  sequence: number;
  dataBase64: string;
  byteLength: number;
  capturedBytes: number;
  capturedMilliseconds: number;
  sampleRate: 16000;
  channels: 1;
  encoding: 'pcm_s16le';
}

interface NativeVoiceCapturePlugin {
  getStatus(): Promise<{ capturing: boolean }>;
  requestMicrophonePermission(): Promise<{ state: string }>;
  start(options: { sampleRate: 16000; chunkMilliseconds: number; maxSeconds: number }): Promise<unknown>;
  stop(): Promise<{ stopped?: boolean }>;
  addListener(eventName: 'voicePcmChunk', listener: (event: NativeVoiceChunkEvent) => void): Promise<PluginListenerHandle>;
  addListener(eventName: 'voiceCaptureError', listener: (event: { message: string }) => void): Promise<PluginListenerHandle>;
  addListener(eventName: 'voiceCaptureStopped', listener: () => void): Promise<PluginListenerHandle>;
}

const NativeVoiceCapture = registerPlugin<NativeVoiceCapturePlugin>('VoiceCapture');

function isNativeAndroidVoiceCapture(): boolean {
  return Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android';
}

function base64Pcm(base64: string): ArrayBuffer {
  const binary = globalThis.atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

export class CapacitorVoiceAudioCaptureAdapter implements VoiceAudioCapturePort {
  private listeners: PluginListenerHandle[] = [];
  private capturing = false;

  get supported(): boolean {
    return isNativeAndroidVoiceCapture();
  }

  get active(): boolean {
    return this.capturing;
  }

  async start(onChunk: VoicePcmChunkHandler, onError?: (error: unknown) => void): Promise<void> {
    if (!this.supported) throw new Error('voice.capture.native_not_supported');
    if (this.active) throw new Error('voice.capture.already_active');
    const permission = await NativeVoiceCapture.requestMicrophonePermission();
    if (permission.state !== 'granted') throw new Error('voice.capture.microphone_permission_required');
    this.listeners = [
      await NativeVoiceCapture.addListener('voicePcmChunk', (event) => {
        if (event.sampleRate !== VOICE_PCM_SAMPLE_RATE || event.channels !== 1 || event.encoding !== 'pcm_s16le') {
          onError?.(new Error('voice.capture.native_media_contract_mismatch'));
          return;
        }
        onChunk(base64Pcm(event.dataBase64));
      }),
      await NativeVoiceCapture.addListener('voiceCaptureError', (event) => {
        this.capturing = false;
        onError?.(new Error(event.message || 'voice.capture.native_failed'));
      }),
      await NativeVoiceCapture.addListener('voiceCaptureStopped', () => {
        this.capturing = false;
      }),
    ];
    try {
      await NativeVoiceCapture.start({ sampleRate: 16_000, chunkMilliseconds: 500, maxSeconds: 120 });
      this.capturing = true;
    } catch (error) {
      await this.removeListeners();
      throw error;
    }
  }

  async stop(): Promise<void> {
    try {
      if (this.supported) {
        const result = await NativeVoiceCapture.stop();
        if (result.stopped === false) throw new Error('voice.capture.native_stop_timeout');
      }
    } finally {
      this.capturing = false;
      // Let the Capacitor bridge deliver a final chunk queued before stop resolved.
      await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
      await this.removeListeners();
    }
  }

  private async removeListeners(): Promise<void> {
    await Promise.all(this.listeners.map((listener) => listener.remove()));
    this.listeners = [];
  }
}

export class BrowserVoiceBatchRecordingAdapter implements VoiceBatchRecordingPort {
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private result: Promise<Blob> | null = null;
  private resolveResult: ((blob: Blob) => void) | null = null;
  private rejectResult: ((error: unknown) => void) | null = null;

  get supported(): boolean {
    return Boolean(globalThis.navigator?.mediaDevices?.getUserMedia && globalThis.MediaRecorder);
  }

  get active(): boolean {
    return this.recorder?.state === 'recording';
  }

  async start(): Promise<void> {
    if (this.active) throw new Error('voice.recording.already_active');
    if (!this.supported) throw new Error('voice.recording.browser_not_supported');
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.chunks = [];
    const mimeType = this.preferredMimeType();
    this.recorder = mimeType ? new MediaRecorder(this.stream, { mimeType }) : new MediaRecorder(this.stream);
    this.result = new Promise<Blob>((resolve, reject) => {
      this.resolveResult = resolve;
      this.rejectResult = reject;
    });
    this.recorder.ondataavailable = (event) => {
      if (event.data.size) this.chunks.push(event.data);
    };
    this.recorder.onerror = (event) => {
      this.rejectResult?.(new Error((event as ErrorEvent).message || 'voice.recording.failed'));
      this.cleanup();
    };
    this.recorder.onstop = () => {
      const type = this.recorder?.mimeType || mimeType || 'audio/webm';
      this.resolveResult?.(new Blob(this.chunks, { type }));
      this.cleanup();
    };
    this.recorder.start(500);
  }

  async stop(): Promise<Blob> {
    if (!this.recorder || !this.result) throw new Error('voice.recording.not_active');
    const result = this.result;
    if (this.recorder.state !== 'inactive') this.recorder.stop();
    return result;
  }

  async cancel(): Promise<void> {
    if (!this.recorder) return;
    const result = this.result;
    if (this.recorder.state !== 'inactive') this.recorder.stop();
    try {
      await result;
    } catch {
      // Cancellation deliberately discards recorder errors and buffered audio.
    }
  }

  private preferredMimeType(): string {
    const choices = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
    return choices.find((type) => MediaRecorder.isTypeSupported(type)) || '';
  }

  private cleanup(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.recorder = null;
    this.resolveResult = null;
    this.rejectResult = null;
  }
}

export class CapacitorVoiceBatchRecordingAdapter implements VoiceBatchRecordingPort {
  private readonly capture = new CapacitorVoiceAudioCaptureAdapter();
  private chunks: ArrayBuffer[] = [];

  get supported(): boolean {
    return this.capture.supported;
  }

  get active(): boolean {
    return this.capture.active;
  }

  async start(): Promise<void> {
    this.chunks = [];
    await this.capture.start((chunk) => this.chunks.push(chunk));
  }

  async stop(): Promise<Blob> {
    await this.capture.stop();
    const bytes = this.chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
    const wav = pcm16ChunksToWav(this.chunks, bytes);
    this.chunks = [];
    return new Blob([wav], { type: 'audio/wav' });
  }

  async cancel(): Promise<void> {
    await this.capture.stop();
    this.chunks = [];
  }
}

export function pcm16ChunksToWav(chunks: ArrayBuffer[], pcmBytes: number): ArrayBuffer {
  const output = new ArrayBuffer(44 + pcmBytes);
  const view = new DataView(output);
  const ascii = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  ascii(0, 'RIFF');
  view.setUint32(4, 36 + pcmBytes, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, VOICE_PCM_SAMPLE_RATE, true);
  view.setUint32(28, VOICE_PCM_SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, 'data');
  view.setUint32(40, pcmBytes, true);
  let offset = 44;
  for (const chunk of chunks) {
    new Uint8Array(output, offset, chunk.byteLength).set(new Uint8Array(chunk));
    offset += chunk.byteLength;
  }
  return output;
}

export const VOICE_AUDIO_CAPTURE = new InjectionToken<VoiceAudioCapturePort>('VOICE_AUDIO_CAPTURE', {
  providedIn: 'root',
  factory: () => isNativeAndroidVoiceCapture()
    ? new CapacitorVoiceAudioCaptureAdapter()
    : new BrowserVoiceAudioCaptureAdapter(),
});

export const VOICE_BATCH_RECORDING = new InjectionToken<VoiceBatchRecordingPort>('VOICE_BATCH_RECORDING', {
  providedIn: 'root',
  factory: () => isNativeAndroidVoiceCapture()
    ? new CapacitorVoiceBatchRecordingAdapter()
    : new BrowserVoiceBatchRecordingAdapter(),
});
