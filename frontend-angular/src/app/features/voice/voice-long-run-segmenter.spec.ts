import { VoiceLongRunPcmSegmenter } from './voice-long-run-segmenter';

describe('VoiceLongRunPcmSegmenter', () => {
  it('keeps overlap inside the 120-second segment budget', () => {
    const segmenter = new VoiceLongRunPcmSegmenter({
      segmentDurationSeconds: 120,
      overlapMilliseconds: 1_000,
      maxDurationSeconds: 600,
      bytesPerSecond: 2,
    });

    const first = segmenter.push(new ArrayBuffer(240));
    const second = segmenter.push(new ArrayBuffer(238));

    expect(first).toEqual([expect.objectContaining({
      sequence: 0, startedAtMs: 0, endedAtMs: 120_000, durationMs: 120_000, overlapMs: 0,
    })]);
    expect(second).toEqual([expect.objectContaining({
      sequence: 1, startedAtMs: 119_000, endedAtMs: 239_000, durationMs: 120_000, overlapMs: 1_000,
    })]);
    expect(second[0].pcmBytes).toBe(240);
  });

  it('clips input at eight hours and emits one bounded final segment', () => {
    const segmenter = new VoiceLongRunPcmSegmenter({
      segmentDurationSeconds: 120,
      overlapMilliseconds: 0,
      maxDurationSeconds: 28_800,
      bytesPerSecond: 2,
    });
    const sequences: number[] = [];

    for (let index = 0; index < 240; index += 1) {
      sequences.push(...segmenter.push(new ArrayBuffer(240)).map((segment) => segment.sequence));
    }
    const final = segmenter.flush();

    expect(sequences).toEqual(Array.from({ length: 240 }, (_value, index) => index));
    expect(final).toBeNull();
    expect(segmenter.reachedLimit).toBe(true);
    expect(segmenter.capturedDurationMs).toBe(28_800_000);
    expect(segmenter.bufferedByteLength).toBe(0);
  });

  it('covers a full eight-hour timeline with the default one-second overlap', () => {
    const segmenter = new VoiceLongRunPcmSegmenter({
      segmentDurationSeconds: 120,
      overlapMilliseconds: 1_000,
      maxDurationSeconds: 28_800,
      bytesPerSecond: 2,
    });

    const complete = segmenter.push(new ArrayBuffer(28_800 * 2));
    const tail = segmenter.flush();
    const segments = tail ? [...complete, tail] : complete;

    expect(segments).toHaveLength(243);
    expect(segments.map((item) => item.sequence))
      .toEqual(Array.from({ length: 243 }, (_value, index) => index));
    expect(segments.every((item) => item.durationMs <= 120_000)).toBe(true);
    expect(segments.every((item) => item.pcmBytes > item.overlapMs * 2 / 1_000)).toBe(true);
    expect(segments.at(-1)?.endedAtMs).toBe(28_800_000);
    expect(segmenter.reachedLimit).toBe(true);
  });

  it('does not emit a copied overlap twice when capture ends on a boundary', () => {
    const segmenter = new VoiceLongRunPcmSegmenter({
      segmentDurationSeconds: 60,
      overlapMilliseconds: 1_000,
      maxDurationSeconds: 120,
      bytesPerSecond: 2,
    });

    expect(segmenter.push(new ArrayBuffer(120))).toHaveLength(1);
    expect(segmenter.flush()).toBeNull();
  });

  it('clips a resumed capture to the remaining absolute run timeline', () => {
    const segmenter = new VoiceLongRunPcmSegmenter({
      segmentDurationSeconds: 120,
      overlapMilliseconds: 1_000,
      maxDurationSeconds: 28_800,
      initialSequence: 240,
      initialTimelineMilliseconds: 28_740_000,
      bytesPerSecond: 2,
    });

    expect(segmenter.push(new ArrayBuffer(240))).toEqual([]);
    expect(segmenter.flush()).toEqual(expect.objectContaining({
      sequence: 240,
      startedAtMs: 28_740_000,
      endedAtMs: 28_800_000,
      durationMs: 60_000,
      pcmBytes: 120,
    }));
    expect(segmenter.reachedLimit).toBe(true);
    expect(segmenter.capturedDurationMs).toBe(28_800_000);
  });
});
