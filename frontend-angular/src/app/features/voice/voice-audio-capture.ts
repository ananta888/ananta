import { InjectionToken } from '@angular/core';
import { Capacitor, PluginListenerHandle, registerPlugin } from '@capacitor/core';

import {
  acquireBrowserAudioStream,
  BrowserAudioStreamLease,
  browserCaptureSourceSupported,
  VoiceCaptureSource,
} from './voice-browser-audio-source';

export type { VoiceCaptureSource } from './voice-browser-audio-source';

export const VOICE_PCM_MEDIA_TYPE = 'audio/pcm;rate=16000;channels=1';
export const VOICE_PCM_SAMPLE_RATE = 16_000;

export type VoicePcmChunkHandler = (chunk: ArrayBuffer) => void;
export type VoiceCaptureStoppedHandler = (reason?: string) => void;

export interface VoiceAudioCaptureOptions {
  /** Omitted for the existing short Live mode, whose native default is 120s. */
  maxDurationSeconds?: number;
}

export interface VoiceBatchRecordingObserver {
  ended?(reason?: string): void;
  error?(error: unknown): void;
}

/** Platform seam: Capacitor can provide a native AudioRecord implementation. */
export interface VoiceAudioCapturePort {
  readonly supported: boolean;
  readonly active: boolean;
  readonly prepared: boolean;
  supportsSource(source: VoiceCaptureSource): boolean;
  refreshCapabilities?(): Promise<void>;
  prepare(source: VoiceCaptureSource): Promise<void>;
  start(
    onChunk: VoicePcmChunkHandler,
    onError?: (error: unknown) => void,
    onStopped?: VoiceCaptureStoppedHandler,
    options?: VoiceAudioCaptureOptions,
  ): Promise<void>;
  stop(): Promise<void>;
}

/** Platform seam for a complete recording sent to the canonical Hub batch API. */
export interface VoiceBatchRecordingPort {
  readonly supported: boolean;
  readonly active: boolean;
  supportsSource(source: VoiceCaptureSource): boolean;
  refreshCapabilities?(): Promise<void>;
  start(source?: VoiceCaptureSource, observer?: VoiceBatchRecordingObserver): Promise<void>;
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

export function float32ChannelsToPcm16(
  channels: readonly Float32Array[],
  sourceSampleRate: number,
  targetSampleRate = VOICE_PCM_SAMPLE_RATE,
): ArrayBuffer {
  if (!channels.length) return new ArrayBuffer(0);
  const frameCount = channels[0].length;
  if (channels.some((channel) => channel.length !== frameCount)) {
    throw new Error('voice.capture.channel_length_mismatch');
  }
  if (channels.length === 1) {
    return float32MonoToPcm16(channels[0], sourceSampleRate, targetSampleRate);
  }
  const mono = new Float32Array(frameCount);
  for (const channel of channels) {
    for (let index = 0; index < frameCount; index += 1) mono[index] += channel[index];
  }
  const scale = 1 / channels.length;
  for (let index = 0; index < frameCount; index += 1) mono[index] *= scale;
  return float32MonoToPcm16(mono, sourceSampleRate, targetSampleRate);
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
  private lease: BrowserAudioStreamLease | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private sink: GainNode | null = null;
  private accumulator = new Pcm16ChunkAccumulator();
  private onChunk: VoicePcmChunkHandler | null = null;
  private onError: ((error: unknown) => void) | null = null;
  private onStopped: VoiceCaptureStoppedHandler | null = null;
  private removeEndedHandler: (() => void) | null = null;
  private operationGeneration = 0;

  get supported(): boolean {
    return this.supportsSource('microphone');
  }

  get active(): boolean {
    return this.context !== null;
  }

  get prepared(): boolean {
    return this.lease !== null;
  }

  supportsSource(source: VoiceCaptureSource): boolean {
    return Boolean(globalThis.AudioContext && browserCaptureSourceSupported(source));
  }

  async prepare(source: VoiceCaptureSource): Promise<void> {
    if (this.active || this.prepared) throw new Error('voice.capture.already_active');
    if (!this.supportsSource(source)) {
      throw new Error(source === 'system_audio'
        ? 'voice.capture.system_audio_not_supported'
        : 'voice.capture.browser_not_supported');
    }
    const generation = ++this.operationGeneration;
    const lease = await acquireBrowserAudioStream(source);
    if (generation !== this.operationGeneration) {
      lease.release();
      throw new Error('voice.capture.cancelled');
    }
    this.lease = lease;
  }

  async start(
    onChunk: VoicePcmChunkHandler,
    onError?: (error: unknown) => void,
    onStopped?: VoiceCaptureStoppedHandler,
    _options?: VoiceAudioCaptureOptions,
  ): Promise<void> {
    if (this.active) throw new Error('voice.capture.already_active');
    if (!this.lease) throw new Error('voice.capture.not_prepared');

    try {
      if (this.lease.ended) throw new Error('voice.capture.source_ended');
      this.onChunk = onChunk;
      this.onError = onError || null;
      this.onStopped = onStopped || null;
      this.accumulator = new Pcm16ChunkAccumulator();
      this.context = new AudioContext({ sampleRate: VOICE_PCM_SAMPLE_RATE });
      await this.context.resume();
      this.source = this.context.createMediaStreamSource(this.lease.audioStream);
      this.removeEndedHandler = this.lease.onEnded(() => {
        this.reportStopped('source_ended');
      });
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
    this.operationGeneration += 1;
    this.onError = null;
    this.onStopped = null;
    const context = this.context;
    this.removeEndedHandler?.();
    this.worklet?.port.close();
    this.worklet?.disconnect();
    if (this.processor) this.processor.onaudioprocess = null;
    this.processor?.disconnect();
    this.source?.disconnect();
    this.sink?.disconnect();
    this.flushPcm();
    this.lease?.release();
    this.worklet = null;
    this.processor = null;
    this.source = null;
    this.sink = null;
    this.lease = null;
    this.context = null;
    this.onChunk = null;
    this.removeEndedHandler = null;
    if (context && context.state !== 'closed') await context.close();
  }

  private async startWorklet(onChunk: VoicePcmChunkHandler): Promise<void> {
    if (!this.context || !this.source || !this.sink) return;
    const source = `
      class AnantaVoiceCaptureProcessor extends AudioWorkletProcessor {
        process(inputs) {
          const channels = inputs[0] || [];
          const length = channels[0] ? channels[0].length : 0;
          if (length) {
            const mono = new Float32Array(length);
            for (const channel of channels) {
              for (let index = 0; index < length; index += 1) mono[index] += channel[index];
            }
            const scale = 1 / Math.max(1, channels.length);
            for (let index = 0; index < length; index += 1) mono[index] *= scale;
            this.port.postMessage(mono);
          }
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
    this.processor = this.context.createScriptProcessor(4096, 2, 1);
    this.processor.onaudioprocess = (event: AudioProcessingEvent) => {
      try {
        const channels = Array.from(
          { length: event.inputBuffer.numberOfChannels },
          (_value, index) => event.inputBuffer.getChannelData(index),
        );
        const pcm = float32ChannelsToPcm16(channels, event.inputBuffer.sampleRate);
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

  private reportStopped(reason: string): void {
    const handler = this.onStopped;
    this.onStopped = null;
    handler?.(reason);
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

interface NativeVoiceStoppedEvent {
  reason?: string;
}

interface NativePcmCapturePlugin {
  start(options: { sampleRate: 16000; chunkMilliseconds: number; maxSeconds: number }): Promise<unknown>;
  stop(): Promise<{ stopped?: boolean }>;
  addListener(eventName: 'voicePcmChunk', listener: (event: NativeVoiceChunkEvent) => void): Promise<PluginListenerHandle>;
  addListener(
    eventName: 'voiceCaptureError',
    listener: (event: { code?: string; message: string }) => void,
  ): Promise<PluginListenerHandle>;
  addListener(
    eventName: 'voiceCaptureStopped',
    listener: (event: NativeVoiceStoppedEvent) => void,
  ): Promise<PluginListenerHandle>;
}

interface NativeVoiceCapturePlugin extends NativePcmCapturePlugin {
  getStatus(): Promise<{ capturing: boolean }>;
  requestMicrophonePermission(): Promise<{ state: string }>;
}

interface NativePlaybackAudioCapturePlugin extends NativePcmCapturePlugin {
  getStatus(): Promise<{ supported: boolean; prepared: boolean; capturing: boolean }>;
  prepare(): Promise<{ prepared: boolean }>;
}

const NativeVoiceCapture = registerPlugin<NativeVoiceCapturePlugin>('VoiceCapture');
const NativePlaybackAudioCapture = registerPlugin<NativePlaybackAudioCapturePlugin>('PlaybackAudioCapture');

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
  private preparedSource: VoiceCaptureSource | null = null;
  private preparing: { source: VoiceCaptureSource; generation: number } | null = null;
  private operationGeneration = 0;
  private drainingGeneration: number | null = null;
  private stopInFlight: Promise<void> | null = null;
  private stopRequested = false;
  private playbackCaptureSupported = false;

  get supported(): boolean {
    return isNativeAndroidVoiceCapture();
  }

  get active(): boolean {
    return this.capturing;
  }

  get prepared(): boolean {
    return this.preparedSource !== null;
  }

  supportsSource(source: VoiceCaptureSource): boolean {
    return this.supported && (source === 'microphone' || this.playbackCaptureSupported);
  }

  async refreshCapabilities(): Promise<void> {
    if (!this.supported) {
      this.playbackCaptureSupported = false;
      return;
    }
    try {
      const status = await NativePlaybackAudioCapture.getStatus();
      this.playbackCaptureSupported = status.supported === true;
    } catch {
      this.playbackCaptureSupported = false;
    }
  }

  async prepare(source: VoiceCaptureSource): Promise<void> {
    if (this.active || this.prepared || this.preparing) throw new Error('voice.capture.already_active');
    const generation = ++this.operationGeneration;
    this.preparing = { source, generation };
    try {
      if (source === 'system_audio') {
        await this.refreshCapabilities();
        this.ensureNativeOperation(generation);
      }
      if (!this.supportsSource(source)) throw new Error('voice.capture.native_not_supported');
      if (source === 'microphone') {
        const permission = await NativeVoiceCapture.requestMicrophonePermission();
        if (permission.state !== 'granted') {
          throw new Error('voice.capture.microphone_permission_required');
        }
      } else {
        const result = await NativePlaybackAudioCapture.prepare();
        if (result.prepared !== true) throw new Error('voice.capture.system_audio_permission_required');
      }
      if (generation !== this.operationGeneration) {
        if (source === 'system_audio') await NativePlaybackAudioCapture.stop().catch(() => undefined);
        throw new Error('voice.capture.cancelled');
      }
      this.preparedSource = source;
    } finally {
      if (this.preparing?.generation === generation) this.preparing = null;
    }
  }

  async start(
    onChunk: VoicePcmChunkHandler,
    onError?: (error: unknown) => void,
    onStopped?: VoiceCaptureStoppedHandler,
    options?: VoiceAudioCaptureOptions,
  ): Promise<void> {
    if (!this.supported) throw new Error('voice.capture.native_not_supported');
    if (this.active) throw new Error('voice.capture.already_active');
    if (!this.preparedSource) throw new Error('voice.capture.not_prepared');
    const generation = this.operationGeneration;
    const plugin = this.preparedSource === 'system_audio'
      ? NativePlaybackAudioCapture
      : NativeVoiceCapture;
    this.stopRequested = false;
    this.listeners = [];
    try {
      this.listeners.push(await plugin.addListener('voicePcmChunk', (event) => {
        if (generation !== this.operationGeneration && generation !== this.drainingGeneration) return;
        if (event.sampleRate !== VOICE_PCM_SAMPLE_RATE || event.channels !== 1 || event.encoding !== 'pcm_s16le') {
          onError?.(new Error('voice.capture.native_media_contract_mismatch'));
          return;
        }
        onChunk(base64Pcm(event.dataBase64));
      }));
      this.ensureNativeOperation(generation);
      this.listeners.push(await plugin.addListener('voiceCaptureError', (event) => {
        if (generation !== this.operationGeneration) return;
        this.capturing = false;
        onError?.({
          code: event.code || 'voice.capture.native_failed',
          message: event.message || 'Die native Audioaufnahme ist fehlgeschlagen.',
        });
      }));
      this.ensureNativeOperation(generation);
      this.listeners.push(await plugin.addListener('voiceCaptureStopped', (event) => {
        if (generation !== this.operationGeneration) return;
        this.capturing = false;
        if (!this.stopRequested) onStopped?.(event.reason);
      }));
      this.ensureNativeOperation(generation);
      const requestedMaxSeconds = options?.maxDurationSeconds;
      const maxSeconds = requestedMaxSeconds == null
        ? 120
        : Math.max(1, Math.min(28_800, Math.round(requestedMaxSeconds)));
      await plugin.start({ sampleRate: 16_000, chunkMilliseconds: 500, maxSeconds });
      if (generation !== this.operationGeneration) {
        await this.stop().catch(() => undefined);
        throw new Error('voice.capture.cancelled');
      }
      this.capturing = true;
    } catch (error) {
      await this.stop().catch(() => undefined);
      throw error;
    }
  }

  async stop(): Promise<void> {
    if (this.stopInFlight) return this.stopInFlight;
    const operation = this.stopInternal();
    this.stopInFlight = operation;
    try {
      await operation;
    } finally {
      if (this.stopInFlight === operation) this.stopInFlight = null;
    }
  }

  private async stopInternal(): Promise<void> {
    const stoppingGeneration = this.operationGeneration;
    if (this.capturing) this.drainingGeneration = stoppingGeneration;
    this.operationGeneration += 1;
    const source = this.preparedSource || this.preparing?.source;
    this.preparing = null;
    this.stopRequested = true;
    try {
      if (this.supported && source) {
        const plugin = source === 'system_audio'
          ? NativePlaybackAudioCapture
          : NativeVoiceCapture;
        const result = await plugin.stop();
        if (result.stopped === false) throw new Error('voice.capture.native_stop_timeout');
      }
    } finally {
      this.capturing = false;
      this.preparedSource = null;
      // Native stop resolves only after the service queued its final partial PCM chunk.
      // Keep that generation eligible for one JavaScript turn before removing listeners.
      await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
      await this.removeListeners();
      if (this.drainingGeneration === stoppingGeneration) this.drainingGeneration = null;
    }
  }

  private async removeListeners(): Promise<void> {
    const listeners = this.listeners;
    this.listeners = [];
    await Promise.all(listeners.map(async (listener) => {
      await listener.remove().catch(() => undefined);
    }));
  }

  private ensureNativeOperation(generation: number): void {
    if (generation !== this.operationGeneration) throw new Error('voice.capture.cancelled');
  }
}

export class BrowserVoiceBatchRecordingAdapter implements VoiceBatchRecordingPort {
  private lease: BrowserAudioStreamLease | null = null;
  private recorder: MediaRecorder | null = null;
  private removeEndedHandler: (() => void) | null = null;
  private chunks: Blob[] = [];
  private result: Promise<Blob> | null = null;
  private resolveResult: ((blob: Blob) => void) | null = null;
  private rejectResult: ((error: unknown) => void) | null = null;
  private observer: VoiceBatchRecordingObserver | null = null;
  private acquiringGeneration: number | null = null;
  private operationGeneration = 0;
  private endedBySource = false;

  get supported(): boolean {
    return this.supportsSource('microphone');
  }

  get active(): boolean {
    return this.recorder?.state === 'recording';
  }

  supportsSource(source: VoiceCaptureSource): boolean {
    return Boolean(globalThis.MediaRecorder && browserCaptureSourceSupported(source));
  }

  async start(
    source: VoiceCaptureSource = 'microphone',
    observer: VoiceBatchRecordingObserver = {},
  ): Promise<void> {
    if (this.active || this.result || this.lease || this.acquiringGeneration !== null) {
      throw new Error('voice.recording.already_active');
    }
    if (!this.supportsSource(source)) {
      throw new Error(source === 'system_audio'
        ? 'voice.capture.system_audio_not_supported'
        : 'voice.recording.browser_not_supported');
    }
    const generation = ++this.operationGeneration;
    this.acquiringGeneration = generation;
    let lease: BrowserAudioStreamLease;
    try {
      lease = await acquireBrowserAudioStream(source);
    } catch (error) {
      if (this.acquiringGeneration === generation) this.acquiringGeneration = null;
      throw error;
    }
    if (generation !== this.operationGeneration) {
      lease.release();
      throw new Error('voice.capture.cancelled');
    }
    this.acquiringGeneration = null;
    this.lease = lease;
    this.observer = observer;
    this.endedBySource = false;
    try {
      this.chunks = [];
      const mimeType = this.preferredMimeType();
      this.recorder = mimeType
        ? new MediaRecorder(this.lease.audioStream, { mimeType })
        : new MediaRecorder(this.lease.audioStream);
      this.result = new Promise<Blob>((resolve, reject) => {
        this.resolveResult = resolve;
        this.rejectResult = reject;
      });
      this.recorder.ondataavailable = (event) => {
        if (event.data.size) this.chunks.push(event.data);
      };
      this.recorder.onerror = (event) => {
        const error = new Error((event as ErrorEvent).message || 'voice.recording.failed');
        const currentObserver = this.observer;
        this.rejectResult?.(error);
        this.cleanup();
        currentObserver?.error?.(error);
      };
      this.recorder.onstop = () => {
        const type = this.recorder?.mimeType || mimeType || 'audio/webm';
        const currentObserver = this.observer;
        const endedBySource = this.endedBySource;
        this.resolveResult?.(new Blob(this.chunks, { type }));
        this.cleanup();
        if (endedBySource) currentObserver?.ended?.('source_ended');
      };
      this.removeEndedHandler = this.lease.onEnded(() => {
        this.endedBySource = true;
        if (this.recorder?.state === 'recording') this.recorder.stop();
      });
      this.recorder.start(500);
      if (this.lease.ended && this.recorder.state === 'recording') this.recorder.stop();
    } catch (error) {
      this.result = null;
      this.chunks = [];
      this.cleanup();
      throw error;
    }
  }

  async stop(): Promise<Blob> {
    this.operationGeneration += 1;
    this.acquiringGeneration = null;
    if (!this.result) throw new Error('voice.recording.not_active');
    const result = this.result;
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    try {
      return await result;
    } finally {
      this.result = null;
      this.chunks = [];
    }
  }

  async cancel(): Promise<void> {
    this.operationGeneration += 1;
    this.acquiringGeneration = null;
    this.observer = null;
    this.endedBySource = false;
    if (!this.result && !this.lease) return;
    const result = this.result;
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    try {
      await result;
    } catch {
      // Cancellation deliberately discards recorder errors and buffered audio.
    } finally {
      this.result = null;
      this.chunks = [];
      this.cleanup();
    }
  }

  private preferredMimeType(): string {
    const choices = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
    return choices.find((type) => MediaRecorder.isTypeSupported(type)) || '';
  }

  private cleanup(): void {
    this.removeEndedHandler?.();
    this.lease?.release();
    this.lease = null;
    this.recorder = null;
    this.resolveResult = null;
    this.rejectResult = null;
    this.removeEndedHandler = null;
    this.observer = null;
    this.endedBySource = false;
  }
}

export class CapacitorVoiceBatchRecordingAdapter implements VoiceBatchRecordingPort {
  private readonly capture = new CapacitorVoiceAudioCaptureAdapter();
  private chunks: ArrayBuffer[] = [];
  private captureFailure: unknown = null;

  get supported(): boolean {
    return this.capture.supported;
  }

  get active(): boolean {
    return this.capture.active;
  }

  supportsSource(source: VoiceCaptureSource): boolean {
    return this.capture.supportsSource(source);
  }

  async refreshCapabilities(): Promise<void> {
    await this.capture.refreshCapabilities?.();
  }

  async start(
    source: VoiceCaptureSource = 'microphone',
    observer: VoiceBatchRecordingObserver = {},
  ): Promise<void> {
    this.chunks = [];
    this.captureFailure = null;
    try {
      await this.capture.prepare(source);
      await this.capture.start(
        (chunk) => this.chunks.push(chunk),
        (error) => {
          this.captureFailure = error;
          observer.error?.(error);
        },
        (reason) => observer.ended?.(reason),
      );
    } catch (error) {
      await this.capture.stop().catch(() => undefined);
      this.chunks = [];
      this.captureFailure = null;
      throw error;
    }
  }

  async stop(): Promise<Blob> {
    await this.capture.stop();
    if (this.captureFailure) {
      const failure = this.captureFailure;
      this.captureFailure = null;
      this.chunks = [];
      throw failure;
    }
    const bytes = this.chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
    const wav = pcm16ChunksToWav(this.chunks, bytes);
    this.chunks = [];
    return new Blob([wav], { type: 'audio/wav' });
  }

  async cancel(): Promise<void> {
    await this.capture.stop();
    this.chunks = [];
    this.captureFailure = null;
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
