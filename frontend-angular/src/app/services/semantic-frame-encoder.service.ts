
export type SemanticStandardCodec = 'image/webp' | 'image/avif' | 'video/vp9';
export type SemanticFrameKind = 'reference' | 'delta' | 'region_repair';

export interface SemanticEncodeContext {
  readonly sessionId: string;
  readonly contractId: string;
  readonly contractDigest: string;
  readonly leaseId: string;
  readonly leaseDigest: string;
  readonly epoch: number;
  readonly sequence: number;
}

export interface SemanticEncodeRequest extends SemanticEncodeContext {
  readonly frameId: string;
  readonly kind: SemanticFrameKind;
  readonly baseReferenceId: string | null;
  readonly sceneDigest: string;
  readonly codec: SemanticStandardCodec;
  readonly source: unknown;
  readonly estimatedInputBytes: number;
  readonly deadlineMs: number;
  readonly expiresAtMs: number;
  readonly signal?: AbortSignal;
}

export interface StandardVisualEncoderPort {
  encode(source: unknown, codec: SemanticStandardCodec, signal: AbortSignal): Promise<Uint8Array>;
}

export interface SemanticFrameEnvelope {
  readonly schema: 'ananta.semantic-frame.v1';
  readonly frame_id: string;
  readonly session_id: string;
  readonly contract_id: string;
  readonly contract_digest: string;
  readonly lease_id: string;
  readonly lease_digest: string;
  readonly epoch: number;
  readonly sequence: number;
  readonly frame_kind: SemanticFrameKind;
  readonly base_reference_id: string | null;
  readonly scene_digest: string;
  readonly algorithm: Readonly<{ name: 'standard-web-codec'; version: '1.0.0'; codec: SemanticStandardCodec }>;
  readonly encoded_digest: string;
  readonly total_bytes: number;
  readonly created_at_ms: number;
  readonly expires_at_ms: number;
}

export type SemanticEncodeResult =
  | { readonly status: 'encoded'; readonly frame: SemanticFrameEnvelope; readonly bytes: Uint8Array }
  | { readonly status: 'recovery'; readonly reasonCode: string };

export class SemanticFrameEncoderService {
  private readonly references = new Set<string>();
  private readonly operations = new Set<AbortController>();
  private inFlightBytes = 0;
  private generation = 0;

  constructor(
    private readonly maxQueue = 2,
    private readonly maxInFlightBytes = 1024 * 1024,
    private readonly maxOutputBytes = 512 * 1024,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  async encode(request: SemanticEncodeRequest, port: StandardVisualEncoderPort): Promise<SemanticEncodeResult> {
    const reason = this.validate(request);
    if (reason) return Object.freeze({ status: 'recovery', reasonCode: reason });
    if (request.kind !== 'reference' && (!request.baseReferenceId || !this.references.has(request.baseReferenceId))) {
      return Object.freeze({ status: 'recovery', reasonCode: 'base_reference_missing' });
    }
    if (this.operations.size >= this.maxQueue || this.inFlightBytes + request.estimatedInputBytes > this.maxInFlightBytes) {
      return Object.freeze({ status: 'recovery', reasonCode: 'encoder_backpressure' });
    }
    const operation = new AbortController();
    const generation = this.generation;
    const onAbort = (): void => operation.abort();
    request.signal?.addEventListener('abort', onAbort, { once: true });
    this.operations.add(operation);
    this.inFlightBytes += request.estimatedInputBytes;
    try {
      const bytes = await port.encode(request.source, request.codec, operation.signal);
      if (operation.signal.aborted) return Object.freeze({ status: 'recovery', reasonCode: 'encoder_cancelled' });
      if (generation !== this.generation) return Object.freeze({ status: 'recovery', reasonCode: 'scene_cut_cancelled' });
      const now = this.clock();
      if (now > request.deadlineMs) return Object.freeze({ status: 'recovery', reasonCode: 'encoder_deadline_exceeded' });
      if (!(bytes instanceof Uint8Array) || bytes.byteLength < 1 || bytes.byteLength > this.maxOutputBytes) {
        return Object.freeze({ status: 'recovery', reasonCode: 'encoded_payload_invalid' });
      }
      const encodedDigest = await sha256(bytes);
      const frame: SemanticFrameEnvelope = Object.freeze({
        schema: 'ananta.semantic-frame.v1', frame_id: request.frameId, session_id: request.sessionId,
        contract_id: request.contractId, contract_digest: request.contractDigest,
        lease_id: request.leaseId, lease_digest: request.leaseDigest, epoch: request.epoch,
        sequence: request.sequence, frame_kind: request.kind, base_reference_id: request.baseReferenceId,
        scene_digest: request.sceneDigest,
        algorithm: Object.freeze({ name: 'standard-web-codec', version: '1.0.0', codec: request.codec }),
        encoded_digest: encodedDigest, total_bytes: bytes.byteLength,
        created_at_ms: now, expires_at_ms: request.expiresAtMs,
      });
      if (request.kind === 'reference') this.references.add(request.frameId);
      return Object.freeze({ status: 'encoded', frame, bytes });
    } catch {
      return Object.freeze({ status: 'recovery', reasonCode: operation.signal.aborted ? 'encoder_cancelled' : 'encoder_failed' });
    } finally {
      request.signal?.removeEventListener('abort', onAbort);
      this.operations.delete(operation);
      this.inFlightBytes -= request.estimatedInputBytes;
    }
  }

  invalidateForSceneCut(): void {
    this.generation += 1;
    this.references.clear();
    for (const operation of this.operations) operation.abort();
  }

  cancelPending(): void {
    for (const operation of this.operations) operation.abort();
  }

  snapshot(): Readonly<{ queued: number; inFlightBytes: number; references: number }> {
    return Object.freeze({ queued: this.operations.size, inFlightBytes: this.inFlightBytes, references: this.references.size });
  }

  private validate(request: SemanticEncodeRequest): string | null {
    const ids = [request.frameId, request.sessionId, request.contractId, request.leaseId];
    const digests = [request.contractDigest, request.leaseDigest, request.sceneDigest];
    if (ids.some(value => !/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(value))) return 'encoder_context_invalid';
    if (digests.some(value => !/^[0-9a-f]{64}$/.test(value))) return 'encoder_context_invalid';
    if (!Number.isSafeInteger(request.epoch) || request.epoch < 1 || !Number.isSafeInteger(request.sequence)
        || request.sequence < 0 || !Number.isSafeInteger(request.estimatedInputBytes) || request.estimatedInputBytes < 1
        || !Number.isSafeInteger(request.deadlineMs) || !Number.isSafeInteger(request.expiresAtMs)
        || request.expiresAtMs <= this.clock()) return 'encoder_context_invalid';
    if (!['reference', 'delta', 'region_repair'].includes(request.kind)
        || !['image/webp', 'image/avif', 'video/vp9'].includes(request.codec)) return 'unsupported_codec_or_kind';
    if (request.kind === 'reference' && request.baseReferenceId !== null) return 'reference_has_base';
    return null;
  }
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes.slice().buffer as ArrayBuffer);
  return Array.from(new Uint8Array(digest)).map(value => value.toString(16).padStart(2, '0')).join('');
}
