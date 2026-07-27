import { Injectable } from '@angular/core';

import {
  SemanticStandardCodec,
  StandardVisualEncoderPort,
} from './semantic-frame-encoder.service';

export class StandardVisualEncoderError extends Error {
  constructor(readonly reasonCode: string) {
    super(reasonCode);
    this.name = 'StandardVisualEncoderError';
  }
}

/**
 * Browser-local implementation of the standard visual encoder port.
 *
 * It deliberately exposes no transport or authority decisions. WebP/AVIF use
 * the browser canvas encoder and VP9 uses WebCodecs. A browser codec fallback
 * to another MIME type is rejected instead of being mislabeled.
 */
@Injectable({ providedIn: 'root' })
export class BrowserStandardVisualEncoderAdapter
  implements StandardVisualEncoderPort
{
  async encode(
    source: unknown,
    codec: SemanticStandardCodec,
    signal: AbortSignal,
  ): Promise<Uint8Array> {
    assertNotAborted(signal);
    if (isBlob(source)) return exactBlobBytes(source, codec, signal);
    const frame = drawSource(source);
    try {
      if (codec === 'video/vp9') {
        return await encodeVp9(frame.canvas, frame.width, frame.height, signal);
      }
      return await encodeImage(frame.canvas, codec, signal);
    } finally {
      frame.release();
    }
  }
}

interface DrawnFrame {
  readonly canvas: OffscreenCanvas | HTMLCanvasElement;
  readonly width: number;
  readonly height: number;
  release(): void;
}

function drawSource(source: unknown): DrawnFrame {
  const dimensions = sourceDimensions(source);
  if (!dimensions) throw new StandardVisualEncoderError('visual_source_unsupported');
  const { width, height } = dimensions;
  if (
    !Number.isSafeInteger(width) ||
    !Number.isSafeInteger(height) ||
    width < 1 ||
    height < 1 ||
    width * height > 33_177_600
  ) {
    throw new StandardVisualEncoderError('visual_source_dimensions_invalid');
  }
  const canvas = createCanvas(width, height);
  const context = canvas.getContext('2d');
  if (!context) throw new StandardVisualEncoderError('visual_canvas_context_unavailable');
  if (isImageData(source)) {
    context.putImageData(source, 0, 0);
  } else {
    try {
      context.drawImage(source as CanvasImageSource, 0, 0, width, height);
    } catch (error) {
      throw new StandardVisualEncoderError('visual_source_draw_failed');
    }
  }
  return {
    canvas,
    width,
    height,
    release: () => {
      canvas.width = 1;
      canvas.height = 1;
    },
  };
}

async function encodeImage(
  canvas: OffscreenCanvas | HTMLCanvasElement,
  codec: 'image/webp' | 'image/avif',
  signal: AbortSignal,
): Promise<Uint8Array> {
  const blob = isOffscreenCanvas(canvas)
    ? await abortable(canvas.convertToBlob({ type: codec, quality: 0.86 }), signal)
    : await htmlCanvasBlob(canvas, codec, signal);
  if (blob.type !== codec || blob.size < 1) {
    throw new StandardVisualEncoderError('visual_codec_unsupported');
  }
  return readBlob(blob, signal);
}

async function encodeVp9(
  canvas: OffscreenCanvas | HTMLCanvasElement,
  width: number,
  height: number,
  signal: AbortSignal,
): Promise<Uint8Array> {
  if (typeof VideoEncoder === 'undefined' || typeof VideoFrame === 'undefined') {
    throw new StandardVisualEncoderError('visual_codec_unsupported');
  }
  const config: VideoEncoderConfig = {
    codec: 'vp09.00.10.08',
    width,
    height,
    bitrate: Math.max(100_000, Math.min(8_000_000, width * height * 4)),
    framerate: 30,
    latencyMode: 'realtime',
  };
  const support = await abortable(VideoEncoder.isConfigSupported(config), signal);
  if (!support.supported) throw new StandardVisualEncoderError('visual_codec_unsupported');
  const chunks: Uint8Array[] = [];
  let encoderError: DOMException | null = null;
  const encoder = new VideoEncoder({
    output: (chunk) => {
      const bytes = new Uint8Array(chunk.byteLength);
      chunk.copyTo(bytes);
      chunks.push(bytes);
    },
    error: (error) => {
      encoderError = error;
    },
  });
  const abort = (): void => {
    try {
      encoder.close();
    } catch {
      // Closing an already closed WebCodecs encoder is idempotent here.
    }
  };
  signal.addEventListener('abort', abort, { once: true });
  const frame = new VideoFrame(canvas, { timestamp: 0, duration: 33_333 });
  try {
    encoder.configure(support.config ?? config);
    encoder.encode(frame, { keyFrame: true });
    await abortable(encoder.flush(), signal);
    if (encoderError) throw encoderError;
  } finally {
    frame.close();
    signal.removeEventListener('abort', abort);
    abort();
  }
  const total = chunks.reduce((size, chunk) => size + chunk.byteLength, 0);
  if (total < 1) throw new StandardVisualEncoderError('visual_encoder_empty_output');
  const output = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
    chunk.fill(0);
  }
  return output;
}

function htmlCanvasBlob(
  canvas: HTMLCanvasElement,
  codec: 'image/webp' | 'image/avif',
  signal: AbortSignal,
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const abort = (): void => reject(new StandardVisualEncoderError('visual_encoder_cancelled'));
    signal.addEventListener('abort', abort, { once: true });
    canvas.toBlob(
      (blob) => {
        signal.removeEventListener('abort', abort);
        if (signal.aborted) return abort();
        if (!blob) return reject(new StandardVisualEncoderError('visual_codec_unsupported'));
        resolve(blob);
      },
      codec,
      0.86,
    );
  });
}

async function exactBlobBytes(
  blob: Blob,
  codec: SemanticStandardCodec,
  signal: AbortSignal,
): Promise<Uint8Array> {
  if (blob.type !== codec || blob.size < 1) {
    throw new StandardVisualEncoderError('visual_blob_codec_mismatch');
  }
  return readBlob(blob, signal);
}

async function readBlob(blob: Blob, signal: AbortSignal): Promise<Uint8Array> {
  const modern = blob as Blob & { arrayBuffer?: () => Promise<ArrayBuffer> };
  if (typeof modern.arrayBuffer === 'function') {
    return new Uint8Array(await abortable(modern.arrayBuffer(), signal));
  }
  if (typeof FileReader === 'undefined') {
    throw new StandardVisualEncoderError('visual_blob_reader_unavailable');
  }
  const buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    const abort = (): void => reader.abort();
    signal.addEventListener('abort', abort, { once: true });
    reader.onerror = () => {
      signal.removeEventListener('abort', abort);
      reject(new StandardVisualEncoderError('visual_blob_read_failed'));
    };
    reader.onabort = () => {
      signal.removeEventListener('abort', abort);
      reject(new StandardVisualEncoderError('visual_encoder_cancelled'));
    };
    reader.onload = () => {
      signal.removeEventListener('abort', abort);
      if (!(reader.result instanceof ArrayBuffer)) {
        reject(new StandardVisualEncoderError('visual_blob_read_failed'));
        return;
      }
      resolve(reader.result);
    };
    reader.readAsArrayBuffer(blob);
  });
  return new Uint8Array(buffer);
}

function createCanvas(width: number, height: number): OffscreenCanvas | HTMLCanvasElement {
  if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(width, height);
  if (typeof document === 'undefined') {
    throw new StandardVisualEncoderError('visual_canvas_unavailable');
  }
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function sourceDimensions(source: unknown): { width: number; height: number } | null {
  if (isVideoFrame(source)) {
    return {
      width: source.displayWidth || source.codedWidth,
      height: source.displayHeight || source.codedHeight,
    };
  }
  if (isImageData(source) || isImageBitmap(source) || isCanvas(source)) {
    return { width: source.width, height: source.height };
  }
  if (isVideoElement(source)) {
    return { width: source.videoWidth, height: source.videoHeight };
  }
  return null;
}

function isBlob(value: unknown): value is Blob {
  return typeof Blob !== 'undefined' && value instanceof Blob;
}

function isImageData(value: unknown): value is ImageData {
  return typeof ImageData !== 'undefined' && value instanceof ImageData;
}

function isImageBitmap(value: unknown): value is ImageBitmap {
  return typeof ImageBitmap !== 'undefined' && value instanceof ImageBitmap;
}

function isVideoFrame(value: unknown): value is VideoFrame {
  return typeof VideoFrame !== 'undefined' && value instanceof VideoFrame;
}

function isVideoElement(value: unknown): value is HTMLVideoElement {
  return typeof HTMLVideoElement !== 'undefined' && value instanceof HTMLVideoElement;
}

function isCanvas(value: unknown): value is HTMLCanvasElement | OffscreenCanvas {
  return (
    (typeof HTMLCanvasElement !== 'undefined' && value instanceof HTMLCanvasElement) ||
    (typeof OffscreenCanvas !== 'undefined' && value instanceof OffscreenCanvas)
  );
}

function isOffscreenCanvas(
  value: OffscreenCanvas | HTMLCanvasElement,
): value is OffscreenCanvas {
  return typeof OffscreenCanvas !== 'undefined' && value instanceof OffscreenCanvas;
}

function assertNotAborted(signal: AbortSignal): void {
  if (signal.aborted) throw new StandardVisualEncoderError('visual_encoder_cancelled');
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  assertNotAborted(signal);
  return new Promise((resolve, reject) => {
    const abort = (): void => reject(new StandardVisualEncoderError('visual_encoder_cancelled'));
    signal.addEventListener('abort', abort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener('abort', abort);
        if (signal.aborted) return abort();
        resolve(value);
      },
      (error) => {
        signal.removeEventListener('abort', abort);
        reject(error);
      },
    );
  });
}
