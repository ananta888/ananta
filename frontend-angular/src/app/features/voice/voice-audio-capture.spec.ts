import { Pcm16ChunkAccumulator, float32MonoToPcm16, pcm16ChunksToWav } from './voice-audio-capture';

describe('voice audio capture contract', () => {
  it('encodes signed little-endian PCM16 at the requested sample rate', () => {
    const result = float32MonoToPcm16(new Float32Array([-1, -0.5, 0, 0.5, 1]), 16_000);
    const view = new DataView(result);

    expect(result.byteLength).toBe(10);
    expect(view.getInt16(0, true)).toBe(-32768);
    expect(view.getInt16(2, true)).toBe(-16384);
    expect(view.getInt16(4, true)).toBe(0);
    expect(view.getInt16(6, true)).toBe(16384);
    expect(view.getInt16(8, true)).toBe(32767);
  });

  it('downsamples bounded mono chunks to exactly 16 kHz', () => {
    const input = new Float32Array(48_000).fill(0.25);
    const result = float32MonoToPcm16(input, 48_000);

    expect(result.byteLength).toBe(16_000 * 2);
    expect(new DataView(result).getInt16(0, true)).toBe(8192);
  });

  it('rejects unsupported upsampling instead of changing the Hub media contract', () => {
    expect(() => float32MonoToPcm16(new Float32Array([0]), 8_000)).toThrow(
      'voice.capture.invalid_target_sample_rate',
    );
  });

  it('wraps native Android PCM chunks in the canonical mono 16 kHz WAV header', () => {
    const pcm = new Uint8Array([0, 0, 255, 127]).buffer;
    const wav = pcm16ChunksToWav([pcm], pcm.byteLength);
    const bytes = new Uint8Array(wav);
    const view = new DataView(wav);

    expect(new TextDecoder().decode(bytes.slice(0, 4))).toBe('RIFF');
    expect(new TextDecoder().decode(bytes.slice(8, 12))).toBe('WAVE');
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(16_000);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getUint32(40, true)).toBe(4);
    expect([...bytes.slice(44)]).toEqual([0, 0, 255, 127]);
  });

  it('coalesces AudioWorklet render quanta into 500 ms HTTP chunks and flushes an aligned remainder', () => {
    const accumulator = new Pcm16ChunkAccumulator();
    const chunks: ArrayBuffer[] = [];
    const renderQuantum = new ArrayBuffer(128 * 2);
    for (let index = 0; index < 63; index += 1) accumulator.push(renderQuantum, (chunk) => chunks.push(chunk));

    expect(chunks.map((chunk) => chunk.byteLength)).toEqual([16_000]);
    accumulator.flush((chunk) => chunks.push(chunk));
    expect(chunks.map((chunk) => chunk.byteLength)).toEqual([16_000, 128]);
    expect(chunks.every((chunk) => chunk.byteLength % 2 === 0)).toBe(true);
  });

  it('rejects sample-unaligned PCM before it reaches the stream queue', () => {
    const accumulator = new Pcm16ChunkAccumulator();
    expect(() => accumulator.push(new ArrayBuffer(3), () => undefined)).toThrow(
      'voice.capture.unaligned_pcm_chunk',
    );
  });
});
