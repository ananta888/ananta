export const SEMANTIC_VISUAL_CAPTURE_CAPABILITY = 'semantic_visual_capture' as const;

export interface SemanticCaptureLimits {
  readonly maxWidth: number;
  readonly maxHeight: number;
  readonly maxFramesPerSecond: number;
  readonly maxCpuMsPerFrame: number;
  readonly maxGpuMsPerFrame: number;
  readonly maxWorkingBytes: number;
}

export interface SensitiveCaptureRegion {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface SemanticCaptureAuthorization {
  readonly consentId: string;
  readonly sessionId: string;
  readonly epoch: number;
  readonly browserPermission: 'granted' | 'denied' | 'prompt';
  readonly capabilities: readonly string[];
  readonly expiresAtMs: number;
}

export interface CaptureResource {
  readonly byteLength: number;
  close(): void;
}

export interface CaptureIntermediateResource extends CaptureResource {
  readonly kind: 'canvas' | 'bitmap' | 'gpu_buffer';
}

export interface RawCaptureFrame extends CaptureResource {
  readonly width: number;
  readonly height: number;
}

/** A resource whose pixels have already been masked; raw resources never cross this boundary. */
export interface MaskedCaptureFrame extends CaptureResource {
  readonly masked: true;
  readonly width: number;
  readonly height: number;
}

export interface CaptureStreamHandle {
  readonly permission: 'granted' | 'denied';
  nextFrame(signal: AbortSignal): Promise<RawCaptureFrame>;
  close(): void;
}

export interface CaptureStageMeasurement {
  readonly cpuMs: number;
  readonly gpuMs: number;
  readonly workingBytes: number;
}

export interface MaskedFrameResult {
  readonly frame: MaskedCaptureFrame;
  readonly measurement: CaptureStageMeasurement;
  /** Canvas/bitmap/GPU wrappers transferred to the session for deterministic cleanup. */
  readonly intermediates?: readonly CaptureIntermediateResource[];
}

/** Browser-local executor port. It has no transport, lease, or scheduling method. */
export interface SemanticCaptureBackend {
  open(signal: AbortSignal): Promise<CaptureStreamHandle>;
  maskBeforeProcessing(
    raw: RawCaptureFrame,
    regions: readonly SensitiveCaptureRegion[],
    limits: Readonly<SemanticCaptureLimits>,
    signal: AbortSignal,
  ): Promise<MaskedFrameResult>;
}

export type CaptureFailureReason =
  | 'capture_not_open'
  | 'capture_permission_missing'
  | 'capture_capability_missing'
  | 'capture_consent_expired'
  | 'capture_revoked'
  | 'capture_budget_exceeded'
  | 'capture_rate_limited'
  | 'capture_cancelled'
  | 'capture_backend_error';

export class SemanticCaptureError extends Error {
  constructor(readonly reasonCode: CaptureFailureReason, message: string) {
    super(message);
    this.name = 'SemanticCaptureError';
  }
}
