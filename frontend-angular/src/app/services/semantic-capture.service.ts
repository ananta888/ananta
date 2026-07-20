import { Injectable } from '@angular/core';
import {
  CaptureStreamHandle,
  CaptureIntermediateResource,
  MaskedCaptureFrame,
  SemanticCaptureAuthorization,
  SemanticCaptureBackend,
  SemanticCaptureError,
  SemanticCaptureLimits,
  SEMANTIC_VISUAL_CAPTURE_CAPABILITY,
  SensitiveCaptureRegion,
} from './semantic-capture.types';

export const DEFAULT_SEMANTIC_CAPTURE_LIMITS: Readonly<SemanticCaptureLimits> = Object.freeze({
  maxWidth: 1920,
  maxHeight: 1080,
  maxFramesPerSecond: 12,
  maxCpuMsPerFrame: 24,
  maxGpuMsPerFrame: 12,
  maxWorkingBytes: 32 * 1024 * 1024,
});

export interface SemanticCaptureSession {
  dispatch<T>(
    regions: readonly SensitiveCaptureRegion[],
    consumeMasked: (frame: MaskedCaptureFrame, signal: AbortSignal) => Promise<T>,
    nowMs?: number,
  ): Promise<T>;
  revoke(): void;
  replaceTrack(backend: SemanticCaptureBackend, nowMs?: number): Promise<void>;
  destroy(): void;
  readonly closed: boolean;
}

@Injectable({ providedIn: 'root' })
export class SemanticCaptureService {
  async open(
    authorization: Readonly<SemanticCaptureAuthorization>,
    backend: SemanticCaptureBackend,
    limits: Readonly<SemanticCaptureLimits> = DEFAULT_SEMANTIC_CAPTURE_LIMITS,
    nowMs = Date.now(),
  ): Promise<SemanticCaptureSession> {
    validateLimits(limits);
    assertAuthorized(authorization, nowMs);
    const controller = new AbortController();
    let stream: CaptureStreamHandle;
    try {
      stream = await backend.open(controller.signal);
    } catch (error) {
      controller.abort();
      throw toCaptureError(error, 'capture_backend_error');
    }
    if (stream.permission !== 'granted') {
      safeClose(stream);
      controller.abort();
      throw new SemanticCaptureError('capture_permission_missing', 'browser did not grant capture permission');
    }
    return new LocalCaptureSession(authorization, backend, stream, limits, controller, nowMs);
  }
}

class LocalCaptureSession implements SemanticCaptureSession {
  private lastDispatchMs: number;
  private revoked = false;
  private destroyed = false;
  private inFlight?: AbortController;

  constructor(
    private readonly authorization: Readonly<SemanticCaptureAuthorization>,
    private backend: SemanticCaptureBackend,
    private stream: CaptureStreamHandle,
    private readonly limits: Readonly<SemanticCaptureLimits>,
    private readonly lifetime: AbortController,
    openedAtMs: number,
  ) {
    this.lastDispatchMs = openedAtMs - Math.ceil(1000 / limits.maxFramesPerSecond);
  }

  get closed(): boolean { return this.revoked || this.destroyed; }

  async dispatch<T>(
    regions: readonly SensitiveCaptureRegion[],
    consumeMasked: (frame: MaskedCaptureFrame, signal: AbortSignal) => Promise<T>,
    nowMs = Date.now(),
  ): Promise<T> {
    this.assertLive(nowMs);
    validateRegions(regions);
    const minimumInterval = 1000 / this.limits.maxFramesPerSecond;
    if (nowMs - this.lastDispatchMs < minimumInterval) {
      throw new SemanticCaptureError('capture_rate_limited', 'capture frame rate budget exceeded');
    }
    this.lastDispatchMs = nowMs;
    const operation = new AbortController();
    this.inFlight?.abort();
    this.inFlight = operation;
    const onLifetimeAbort = (): void => operation.abort();
    this.lifetime.signal.addEventListener('abort', onLifetimeAbort, { once: true });
    let raw: Awaited<ReturnType<CaptureStreamHandle['nextFrame']>> | undefined;
    let masked: MaskedCaptureFrame | undefined;
    let intermediates: readonly CaptureIntermediateResource[] = [];
    try {
      raw = await this.stream.nextFrame(operation.signal);
      this.assertLive(nowMs);
      if (raw.width > this.limits.maxWidth || raw.height > this.limits.maxHeight
          || raw.byteLength > this.limits.maxWorkingBytes) {
        throw new SemanticCaptureError('capture_budget_exceeded', 'raw capture exceeds configured bounds');
      }
      // The backend's only output is a MaskedCaptureFrame. Downscale, hash,
      // feature and optional model consumers can only run after this await.
      const result = await this.backend.maskBeforeProcessing(raw, regions, this.limits, operation.signal);
      intermediates = result.intermediates ?? [];
      safeClose(raw);
      raw = undefined;
      this.assertLive(nowMs);
      if (result.frame.masked !== true || result.frame.width > this.limits.maxWidth
          || result.frame.height > this.limits.maxHeight
          || result.measurement.cpuMs > this.limits.maxCpuMsPerFrame
          || result.measurement.gpuMs > this.limits.maxGpuMsPerFrame
          || result.measurement.workingBytes > this.limits.maxWorkingBytes
          || result.frame.byteLength > this.limits.maxWorkingBytes) {
        safeClose(result.frame);
        throw new SemanticCaptureError('capture_budget_exceeded', 'masking stage exceeded configured bounds');
      }
      masked = result.frame;
      return await consumeMasked(masked, operation.signal);
    } catch (error) {
      if (operation.signal.aborted || this.closed) {
        throw new SemanticCaptureError('capture_cancelled', 'capture operation was cancelled');
      }
      throw toCaptureError(error, 'capture_backend_error');
    } finally {
      safeClose(raw);
      safeClose(masked);
      for (const resource of intermediates) safeClose(resource);
      this.lifetime.signal.removeEventListener('abort', onLifetimeAbort);
      if (this.inFlight === operation) this.inFlight = undefined;
    }
  }

  revoke(): void {
    if (this.revoked) return;
    this.revoked = true;
    this.closeResources();
  }

  async replaceTrack(backend: SemanticCaptureBackend, nowMs = Date.now()): Promise<void> {
    this.assertLive(nowMs);
    this.inFlight?.abort();
    safeClose(this.stream);
    const replacement = await backend.open(this.lifetime.signal);
    if (replacement.permission !== 'granted') {
      safeClose(replacement);
      this.revoke();
      throw new SemanticCaptureError('capture_permission_missing', 'replacement track lacks browser permission');
    }
    this.backend = backend;
    this.stream = replacement;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.closeResources();
  }

  private assertLive(nowMs: number): void {
    if (this.destroyed) throw new SemanticCaptureError('capture_not_open', 'capture session is destroyed');
    if (this.revoked) throw new SemanticCaptureError('capture_revoked', 'capture consent was revoked');
    assertAuthorized(this.authorization, nowMs);
  }

  private closeResources(): void {
    this.inFlight?.abort();
    this.lifetime.abort();
    safeClose(this.stream);
  }
}

function assertAuthorized(authorization: Readonly<SemanticCaptureAuthorization>, nowMs: number): void {
  if (authorization.browserPermission !== 'granted') {
    throw new SemanticCaptureError('capture_permission_missing', 'explicit browser permission is required');
  }
  if (!authorization.capabilities.includes(SEMANTIC_VISUAL_CAPTURE_CAPABILITY)) {
    throw new SemanticCaptureError('capture_capability_missing', 'semantic visual capture capability is absent');
  }
  if (!Number.isSafeInteger(authorization.epoch) || authorization.epoch < 1
      || !authorization.consentId || !authorization.sessionId) {
    throw new SemanticCaptureError('capture_capability_missing', 'capture authorization is malformed');
  }
  if (!Number.isSafeInteger(authorization.expiresAtMs) || authorization.expiresAtMs <= nowMs) {
    throw new SemanticCaptureError('capture_consent_expired', 'capture consent expired');
  }
}

function validateLimits(limits: Readonly<SemanticCaptureLimits>): void {
  const integers = [limits.maxWidth, limits.maxHeight, limits.maxFramesPerSecond, limits.maxWorkingBytes];
  const finite = [limits.maxCpuMsPerFrame, limits.maxGpuMsPerFrame];
  if (integers.some(value => !Number.isSafeInteger(value) || value < 1)
      || finite.some(value => !Number.isFinite(value) || value <= 0)
      || limits.maxWidth > 7680 || limits.maxHeight > 4320 || limits.maxFramesPerSecond > 60
      || limits.maxWorkingBytes > 128 * 1024 * 1024) {
    throw new SemanticCaptureError('capture_budget_exceeded', 'capture limits are invalid');
  }
}

function validateRegions(regions: readonly SensitiveCaptureRegion[]): void {
  if (regions.length > 128) throw new SemanticCaptureError('capture_budget_exceeded', 'mask region budget exceeded');
  for (const region of regions) {
    const values = [region.x, region.y, region.width, region.height];
    if (values.some(value => !Number.isFinite(value)) || region.x < 0 || region.y < 0
        || region.width <= 0 || region.height <= 0 || region.x + region.width > 1 || region.y + region.height > 1) {
      throw new SemanticCaptureError('capture_budget_exceeded', 'mask region is out of bounds');
    }
  }
}

function toCaptureError(error: unknown, fallback: 'capture_backend_error'): SemanticCaptureError {
  return error instanceof SemanticCaptureError
    ? error
    : new SemanticCaptureError(fallback, error instanceof Error ? error.message : 'capture backend failed');
}

function safeClose(resource: { close(): void } | undefined): void {
  try { resource?.close(); } catch { /* Continue closing the remaining resource bundle. */ }
}
