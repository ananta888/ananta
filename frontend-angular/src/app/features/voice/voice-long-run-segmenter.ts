import { InjectionToken } from '@angular/core';

import { VOICE_PCM_SAMPLE_RATE } from './voice-audio-capture';

const PCM_BYTES_PER_SAMPLE = 2;

export interface VoiceLongRunPcmSegment {
  sequence: number;
  startedAtMs: number;
  endedAtMs: number;
  durationMs: number;
  overlapMs: number;
  pcmBytes: number;
  chunks: ArrayBuffer[];
}

export interface VoiceLongRunSegmenterOptions {
  segmentDurationSeconds: number;
  overlapMilliseconds: number;
  maxDurationSeconds: number;
  /** Test seam. Production always uses PCM16 mono at 16 kHz. */
  bytesPerSecond?: number;
  initialSequence?: number;
  initialTimelineMilliseconds?: number;
}

export type VoiceLongRunSegmenterFactory = (
  options: VoiceLongRunSegmenterOptions,
) => VoiceLongRunPcmSegmenter;

/**
 * Pure, bounded rolling PCM segmenter.
 *
 * A segment's prefix overlap is part of its configured duration. Therefore a
 * 120-second segment with one second overlap advances the source timeline by
 * 119 seconds and can never exceed the Hub's 120-second segment budget.
 */
export class VoiceLongRunPcmSegmenter {
  private readonly bytesPerSecond: number;
  private readonly targetBytes: number;
  private readonly overlapBytes: number;
  private readonly remainingSourceBytes: number;
  private readonly timelineOffsetBytes: number;
  private chunks: Uint8Array[] = [];
  private bufferedBytes = 0;
  private newSourceBytes = 0;
  private consumedSourceBytes = 0;
  private nextSequence: number;

  constructor(options: VoiceLongRunSegmenterOptions) {
    if (!Number.isInteger(options.segmentDurationSeconds)
      || options.segmentDurationSeconds < 60
      || options.segmentDurationSeconds > 120) {
      throw new Error('voice.long_run.segment_duration_invalid');
    }
    if (!Number.isInteger(options.overlapMilliseconds)
      || options.overlapMilliseconds < 0
      || options.overlapMilliseconds > 5_000
      || options.overlapMilliseconds >= options.segmentDurationSeconds * 1_000) {
      throw new Error('voice.long_run.overlap_invalid');
    }
    if (!Number.isInteger(options.maxDurationSeconds)
      || options.maxDurationSeconds < options.segmentDurationSeconds
      || options.maxDurationSeconds > 28_800) {
      throw new Error('voice.long_run.max_duration_invalid');
    }
    this.bytesPerSecond = options.bytesPerSecond
      ?? VOICE_PCM_SAMPLE_RATE * PCM_BYTES_PER_SAMPLE;
    if (!Number.isInteger(this.bytesPerSecond) || this.bytesPerSecond <= 0) {
      throw new Error('voice.long_run.media_contract_invalid');
    }
    const initialSequence = options.initialSequence || 0;
    const initialTimelineMilliseconds = options.initialTimelineMilliseconds || 0;
    if (!Number.isInteger(initialSequence) || initialSequence < 0) {
      throw new Error('voice.long_run.initial_sequence_invalid');
    }
    if (!Number.isInteger(initialTimelineMilliseconds)
      || initialTimelineMilliseconds < 0
      || initialTimelineMilliseconds > options.maxDurationSeconds * 1_000) {
      throw new Error('voice.long_run.initial_timeline_invalid');
    }
    this.targetBytes = this.evenBytes(options.segmentDurationSeconds * this.bytesPerSecond);
    this.overlapBytes = this.evenBytes(options.overlapMilliseconds * this.bytesPerSecond / 1_000);
    this.timelineOffsetBytes = this.evenBytes(
      initialTimelineMilliseconds * this.bytesPerSecond / 1_000,
    );
    this.remainingSourceBytes = Math.max(
      0,
      this.evenBytes(options.maxDurationSeconds * this.bytesPerSecond) - this.timelineOffsetBytes,
    );
    this.nextSequence = initialSequence;
  }

  get reachedLimit(): boolean {
    return this.consumedSourceBytes >= this.remainingSourceBytes;
  }

  get capturedDurationMs(): number {
    return this.milliseconds(this.timelineOffsetBytes + this.consumedSourceBytes);
  }

  get bufferedByteLength(): number {
    return this.bufferedBytes;
  }

  push(chunk: ArrayBuffer): VoiceLongRunPcmSegment[] {
    if (!chunk.byteLength || this.reachedLimit) return [];
    if (chunk.byteLength % PCM_BYTES_PER_SAMPLE !== 0) {
      throw new Error('voice.capture.unaligned_pcm_chunk');
    }
    const ready: VoiceLongRunPcmSegment[] = [];
    const input = new Uint8Array(chunk);
    let offset = 0;
    while (offset < input.byteLength && !this.reachedLimit) {
      const remainingBudget = this.remainingSourceBytes - this.consumedSourceBytes;
      const remainingSegment = this.targetBytes - this.bufferedBytes;
      const take = Math.min(input.byteLength - offset, remainingBudget, remainingSegment);
      if (take <= 0) break;
      const part = input.slice(offset, offset + take);
      this.chunks.push(part);
      this.bufferedBytes += take;
      this.newSourceBytes += take;
      this.consumedSourceBytes += take;
      offset += take;
      if (this.bufferedBytes === this.targetBytes) ready.push(this.rotate());
    }
    return ready;
  }

  flush(): VoiceLongRunPcmSegment | null {
    // After a rotation the buffer contains only copied overlap. Do not emit it
    // again when no new source audio followed.
    if (!this.newSourceBytes) {
      this.resetBuffer();
      return null;
    }
    const segment = this.snapshot();
    this.resetBuffer();
    return segment;
  }

  private rotate(): VoiceLongRunPcmSegment {
    const segment = this.snapshot();
    const overlap = this.tail(this.overlapBytes);
    this.chunks = overlap;
    this.bufferedBytes = overlap.reduce((total, chunk) => total + chunk.byteLength, 0);
    this.newSourceBytes = 0;
    return segment;
  }

  private snapshot(): VoiceLongRunPcmSegment {
    const endedAtBytes = this.timelineOffsetBytes + this.consumedSourceBytes;
    const startedAtBytes = Math.max(this.timelineOffsetBytes, endedAtBytes - this.bufferedBytes);
    const overlapBytes = Math.max(0, this.bufferedBytes - this.newSourceBytes);
    const chunks = this.chunks.map((chunk) => (
      chunk.byteOffset === 0 && chunk.byteLength === chunk.buffer.byteLength
        ? chunk.buffer as ArrayBuffer
        : chunk.slice().buffer
    ));
    return {
      sequence: this.nextSequence++,
      startedAtMs: this.milliseconds(startedAtBytes),
      endedAtMs: this.milliseconds(endedAtBytes),
      durationMs: this.milliseconds(this.bufferedBytes),
      overlapMs: this.milliseconds(overlapBytes),
      pcmBytes: this.bufferedBytes,
      chunks,
    };
  }

  private tail(byteLength: number): Uint8Array[] {
    if (!byteLength) return [];
    const result: Uint8Array[] = [];
    let remaining = Math.min(byteLength, this.bufferedBytes);
    for (let index = this.chunks.length - 1; index >= 0 && remaining > 0; index -= 1) {
      const chunk = this.chunks[index];
      const take = Math.min(remaining, chunk.byteLength);
      result.unshift(chunk.slice(chunk.byteLength - take));
      remaining -= take;
    }
    return result;
  }

  private resetBuffer(): void {
    this.chunks = [];
    this.bufferedBytes = 0;
    this.newSourceBytes = 0;
  }

  private milliseconds(bytes: number): number {
    return Math.round(bytes * 1_000 / this.bytesPerSecond);
  }

  private evenBytes(value: number): number {
    const rounded = Math.floor(value);
    return rounded - (rounded % PCM_BYTES_PER_SAMPLE);
  }
}

export const VOICE_LONG_RUN_SEGMENTER_FACTORY = new InjectionToken<VoiceLongRunSegmenterFactory>(
  'VOICE_LONG_RUN_SEGMENTER_FACTORY',
  { providedIn: 'root', factory: () => (options) => new VoiceLongRunPcmSegmenter(options) },
);
