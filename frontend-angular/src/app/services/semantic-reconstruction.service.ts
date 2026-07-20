import { SemanticFrameEnvelope, SemanticStandardCodec } from './semantic-frame-encoder.service';
import { SemanticScene } from './semantic-scene-model';

export interface SemanticRendererInput {
  readonly receiverId: string;
  readonly sequence: number;
  readonly scene: SemanticScene;
  readonly frame: SemanticFrameEnvelope;
  readonly encodedBlob: Blob;
  readonly baseReferenceBlob?: Blob;
}

export interface SemanticRendererMeasurement {
  readonly renderMs: number;
  readonly workingBytes: number;
  readonly driftScore: number;
  readonly staleRegions: number;
  readonly qualityScore: number;
}

/** Minimal local rendering port: deliberately no transport, Hub, scheduler or reputation methods. */
export interface SemanticRendererPort {
  render(input: Readonly<SemanticRendererInput>, signal: AbortSignal): Promise<SemanticRendererMeasurement>;
}

export interface ReconstructionRequest {
  readonly receiverId: string;
  readonly negotiatedDelayMs: number;
  readonly receivedAtMs: number;
  readonly deadlineMs: number;
  readonly scene: SemanticScene;
  readonly frame: SemanticFrameEnvelope;
  readonly encodedBytes: Uint8Array;
  readonly baseReferenceBytes?: Uint8Array;
}

export interface ReconstructionMetric extends SemanticRendererMeasurement {
  readonly receiverId: string;
  readonly sequence: number;
  readonly codec: SemanticStandardCodec;
  readonly queuedDelayMs: number;
}

export type ReconstructionOutcome =
  | { readonly status: 'queued' }
  | { readonly status: 'rendered'; readonly metric: ReconstructionMetric }
  | { readonly status: 'fallback'; readonly reasonCode: string; readonly sequence: number };

interface QueuedReconstruction extends ReconstructionRequest {
  readonly dueAtMs: number;
  readonly ordinal: number;
  readonly bytesReserved: number;
}

export class SemanticReconstructionService {
  private readonly queue: QueuedReconstruction[] = [];
  private readonly metrics: ReconstructionMetric[] = [];
  private reservedBytes = 0;
  private ordinal = 0;

  constructor(private readonly maxQueue = 64, private readonly maxBytes = 16 * 1024 * 1024, private readonly maxMetrics = 128) {}

  enqueue(request: ReconstructionRequest): ReconstructionOutcome {
    const reason = validateRequest(request);
    if (reason) return Object.freeze({ status: 'fallback', reasonCode: reason, sequence: request.frame.sequence });
    const bytesReserved = request.encodedBytes.byteLength + (request.baseReferenceBytes?.byteLength ?? 0);
    if (this.queue.length >= this.maxQueue || this.reservedBytes + bytesReserved > this.maxBytes) {
      return Object.freeze({ status: 'fallback', reasonCode: 'reconstruction_queue_budget', sequence: request.frame.sequence });
    }
    this.queue.push(Object.freeze({
      ...request,
      encodedBytes: request.encodedBytes.slice(),
      baseReferenceBytes: request.baseReferenceBytes?.slice(),
      dueAtMs: request.receivedAtMs + request.negotiatedDelayMs,
      ordinal: ++this.ordinal,
      bytesReserved,
    }));
    this.queue.sort((a, b) => a.dueAtMs - b.dueAtMs || a.ordinal - b.ordinal);
    this.reservedBytes += bytesReserved;
    return Object.freeze({ status: 'queued' });
  }

  async drainDue(nowMs: number, renderer: SemanticRendererPort): Promise<readonly ReconstructionOutcome[]> {
    const outcomes: ReconstructionOutcome[] = [];
    while (this.queue[0]?.dueAtMs <= nowMs) {
      const item = this.queue.shift()!;
      this.reservedBytes -= item.bytesReserved;
      if (nowMs > item.deadlineMs || item.frame.expires_at_ms <= nowMs) {
        wipe(item);
        outcomes.push(Object.freeze({ status: 'fallback', reasonCode: 'reconstruction_deadline', sequence: item.frame.sequence }));
        continue;
      }
      if (item.frame.frame_kind !== 'reference' && !item.baseReferenceBytes) {
        wipe(item);
        outcomes.push(Object.freeze({ status: 'fallback', reasonCode: 'missing_reference', sequence: item.frame.sequence }));
        continue;
      }
      const controller = new AbortController();
      try {
        const immutableInput = deepFreeze({
          receiverId: item.receiverId,
          sequence: item.frame.sequence,
          scene: item.scene,
          frame: item.frame,
          encodedBlob: new Blob([item.encodedBytes.slice().buffer as ArrayBuffer], { type: item.frame.algorithm.codec }),
          baseReferenceBlob: item.baseReferenceBytes
            ? new Blob([item.baseReferenceBytes.slice().buffer as ArrayBuffer], { type: item.frame.algorithm.codec })
            : undefined,
        }) as unknown as SemanticRendererInput;
        const measured = await renderer.render(immutableInput, controller.signal);
        const reason = validateMeasurement(measured);
        if (reason) {
          outcomes.push(Object.freeze({ status: 'fallback', reasonCode: reason, sequence: item.frame.sequence }));
          continue;
        }
        const metric: ReconstructionMetric = Object.freeze({
          ...measured, receiverId: item.receiverId, sequence: item.frame.sequence,
          codec: item.frame.algorithm.codec, queuedDelayMs: item.negotiatedDelayMs,
        });
        this.metrics.push(metric);
        if (this.metrics.length > this.maxMetrics) this.metrics.splice(0, this.metrics.length - this.maxMetrics);
        outcomes.push(Object.freeze({ status: 'rendered', metric }));
      } catch {
        outcomes.push(Object.freeze({ status: 'fallback', reasonCode: 'renderer_failed', sequence: item.frame.sequence }));
      } finally {
        controller.abort();
        wipe(item);
      }
    }
    return Object.freeze(outcomes);
  }

  clear(receiverId?: string): void {
    for (let index = this.queue.length - 1; index >= 0; index -= 1) {
      if (receiverId !== undefined && this.queue[index].receiverId !== receiverId) continue;
      const [item] = this.queue.splice(index, 1);
      this.reservedBytes -= item.bytesReserved;
      wipe(item);
    }
    if (receiverId === undefined) this.metrics.length = 0;
    else for (let index = this.metrics.length - 1; index >= 0; index -= 1) {
      if (this.metrics[index].receiverId === receiverId) this.metrics.splice(index, 1);
    }
  }

  snapshot(): Readonly<{ queued: number; reservedBytes: number; metrics: number; timers: number }> {
    return Object.freeze({ queued: this.queue.length, reservedBytes: this.reservedBytes, metrics: this.metrics.length, timers: 0 });
  }
}

function validateRequest(request: ReconstructionRequest): string | null {
  if (!Number.isSafeInteger(request.negotiatedDelayMs)
      || request.negotiatedDelayMs < 2_000 || request.negotiatedDelayMs > 20_000) return 'invalid_receiver_delay';
  if (!Number.isSafeInteger(request.receivedAtMs) || !Number.isSafeInteger(request.deadlineMs)
      || request.deadlineMs < request.receivedAtMs || !request.receiverId) return 'invalid_reconstruction_context';
  if (!(request.encodedBytes instanceof Uint8Array) || request.encodedBytes.byteLength !== request.frame.total_bytes
      || request.encodedBytes.byteLength > 512 * 1024) return 'invalid_encoded_bytes';
  if (request.scene.session_id !== request.frame.session_id || request.scene.contract_id !== request.frame.contract_id
      || request.scene.epoch !== request.frame.epoch || request.scene.sequence !== request.frame.sequence) return 'scene_frame_binding_mismatch';
  return null;
}

function validateMeasurement(value: SemanticRendererMeasurement): string | null {
  const values = [value.renderMs, value.workingBytes, value.driftScore, value.staleRegions, value.qualityScore];
  if (values.some(item => !Number.isFinite(item)) || value.renderMs < 0 || value.renderMs > 60_000
      || !Number.isSafeInteger(value.workingBytes) || value.workingBytes < 0 || value.workingBytes > 128 * 1024 * 1024
      || value.driftScore < 0 || value.driftScore > 1 || !Number.isSafeInteger(value.staleRegions)
      || value.staleRegions < 0 || value.staleRegions > 256 || value.qualityScore < 0 || value.qualityScore > 1) {
    return 'invalid_renderer_measurement';
  }
  return null;
}

function wipe(item: QueuedReconstruction): void {
  item.encodedBytes.fill(0); item.baseReferenceBytes?.fill(0);
}
function deepFreeze<T>(value: T): Readonly<T> {
  if (value !== null && typeof value === 'object' && !(value instanceof Uint8Array)) {
    Object.values(value as Record<string, unknown>).forEach(item => deepFreeze(item));
    Object.freeze(value);
  }
  return value;
}
