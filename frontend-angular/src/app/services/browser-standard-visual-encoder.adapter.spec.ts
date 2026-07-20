import {
  BrowserStandardVisualEncoderAdapter,
  StandardVisualEncoderError,
} from './browser-standard-visual-encoder.adapter';

describe('BrowserStandardVisualEncoderAdapter', () => {
  it('accepts only an already encoded Blob with the exact declared standard codec', async () => {
    const adapter = new BrowserStandardVisualEncoderAdapter();
    const signal = new AbortController().signal;
    await expect(
      adapter.encode(new Blob([new Uint8Array([1, 2, 3])], { type: 'image/webp' }), 'image/webp', signal),
    ).resolves.toEqual(new Uint8Array([1, 2, 3]));
    await expect(
      adapter.encode(new Blob([new Uint8Array([1])], { type: 'image/png' }), 'image/webp', signal),
    ).rejects.toMatchObject({ reasonCode: 'visual_blob_codec_mismatch' });
  });

  it('fails cancellation before touching a visual source', async () => {
    const adapter = new BrowserStandardVisualEncoderAdapter();
    const operation = new AbortController();
    operation.abort();
    await expect(adapter.encode({}, 'image/webp', operation.signal)).rejects.toEqual(
      new StandardVisualEncoderError('visual_encoder_cancelled'),
    );
  });

  it('uses the browser image encoder and rejects silent MIME fallback', async () => {
    const originalOffscreen = globalThis.OffscreenCanvas;
    const originalImageData = globalThis.ImageData;
    class FakeImageData {
      readonly width = 2;
      readonly height = 2;
    }
    class FakeCanvas {
      width: number;
      height: number;
      constructor(width: number, height: number) {
        this.width = width;
        this.height = height;
      }
      getContext() {
        return { putImageData: vi.fn(), drawImage: vi.fn() };
      }
      async convertToBlob(options: { type: string }) {
        return new Blob([new Uint8Array([9])], { type: options.type });
      }
    }
    Object.defineProperty(globalThis, 'ImageData', { configurable: true, value: FakeImageData });
    Object.defineProperty(globalThis, 'OffscreenCanvas', { configurable: true, value: FakeCanvas });
    try {
      const adapter = new BrowserStandardVisualEncoderAdapter();
      await expect(
        adapter.encode(new FakeImageData(), 'image/avif', new AbortController().signal),
      ).resolves.toEqual(new Uint8Array([9]));
    } finally {
      Object.defineProperty(globalThis, 'OffscreenCanvas', { configurable: true, value: originalOffscreen });
      Object.defineProperty(globalThis, 'ImageData', { configurable: true, value: originalImageData });
    }
  });

  it('uses a supported WebCodecs VP9 keyframe encoder with bounded copied output', async () => {
    const originals = {
      offscreen: globalThis.OffscreenCanvas,
      imageData: globalThis.ImageData,
      videoEncoder: globalThis.VideoEncoder,
      videoFrame: globalThis.VideoFrame,
    };
    class FakeImageData {
      readonly width = 4;
      readonly height = 4;
    }
    class FakeCanvas {
      width: number;
      height: number;
      constructor(width: number, height: number) {
        this.width = width;
        this.height = height;
      }
      getContext() {
        return { putImageData: vi.fn(), drawImage: vi.fn() };
      }
    }
    class FakeFrame {
      close = vi.fn();
    }
    class FakeEncoder {
      static isConfigSupported = vi.fn(async (config: VideoEncoderConfig) => ({ supported: true, config }));
      private readonly output: (chunk: EncodedVideoChunk) => void;
      constructor(init: VideoEncoderInit) {
        this.output = init.output;
      }
      configure = vi.fn();
      encode = vi.fn(() => {
        this.output({
          byteLength: 2,
          copyTo: (target: AllowSharedBufferSource) => (target as Uint8Array).set([7, 8]),
        } as EncodedVideoChunk);
      });
      flush = vi.fn(async () => undefined);
      close = vi.fn();
    }
    Object.defineProperties(globalThis, {
      ImageData: { configurable: true, value: FakeImageData },
      OffscreenCanvas: { configurable: true, value: FakeCanvas },
      VideoEncoder: { configurable: true, value: FakeEncoder },
      VideoFrame: { configurable: true, value: FakeFrame },
    });
    try {
      const adapter = new BrowserStandardVisualEncoderAdapter();
      await expect(
        adapter.encode(new FakeImageData(), 'video/vp9', new AbortController().signal),
      ).resolves.toEqual(new Uint8Array([7, 8]));
      expect(FakeEncoder.isConfigSupported).toHaveBeenCalledWith(
        expect.objectContaining({ codec: 'vp09.00.10.08', width: 4, height: 4 }),
      );
    } finally {
      Object.defineProperties(globalThis, {
        OffscreenCanvas: { configurable: true, value: originals.offscreen },
        ImageData: { configurable: true, value: originals.imageData },
        VideoEncoder: { configurable: true, value: originals.videoEncoder },
        VideoFrame: { configurable: true, value: originals.videoFrame },
      });
    }
  });
});
