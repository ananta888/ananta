import type { RemoteVideoTrack } from 'livekit-client';

import type { SfuRelease, SfuRemoteVideoHandle } from './sfu-room-session.ports';

export type SfuRemoteVideoUnavailableReason = 'unsubscribed' | 'ended';

interface RenderEntry {
  readonly handle: SfuRemoteVideoHandle;
  readonly publicationRef: string;
  readonly track: RemoteVideoTrack;
  readonly attachments: Map<HTMLVideoElement, SfuRelease>;
  readonly removeEndedListener: SfuRelease;
}

const DEFAULT_HANDLES_MAX = 32;
const ATTACHMENTS_PER_HANDLE_MAX = 4;
const UNAVAILABLE_LISTENERS_MAX = 16;

/** Sole owner of LiveKit RemoteVideoTrack references and attach/detach calls. */
export class LivekitSfuVideoRenderAdapter {
  private readonly entries = new Map<string, RenderEntry>();
  private readonly handleByTrack = new WeakMap<object, string>();
  private readonly handleByMediaTrack = new WeakMap<object, string>();
  private readonly targets = new Map<HTMLVideoElement, SfuRelease>();
  private readonly unavailableListeners = new Set<(
    value: Readonly<{ handleId: string; reason: SfuRemoteVideoUnavailableReason }>,
  ) => void>();
  private serial = 0;
  private destroyed = false;

  constructor(private readonly handlesMax = DEFAULT_HANDLES_MAX) {
    if (!Number.isSafeInteger(handlesMax) || handlesMax < 1 || handlesMax > 256) {
      throw new Error('sfu_video_registry_limit_invalid');
    }
  }

  register(
    track: RemoteVideoTrack,
    publicationRef: string,
    source: 'camera' | 'screen',
  ): SfuRemoteVideoHandle | null {
    this.ensureOpen();
    const existingId = this.handleByTrack.get(track as object);
    if (existingId) return this.entries.get(existingId)?.handle ?? null;
    if (!publicationRef || publicationRef.length > 128 || this.entries.size >= this.handlesMax) return null;
    const handle: SfuRemoteVideoHandle = Object.freeze({
      handleId: `sfu-video-${++this.serial}`,
      source,
    });
    const ended = () => { this.releaseHandle(handle.handleId, 'ended'); };
    const mediaTrack = track.mediaStreamTrack;
    mediaTrack.addEventListener?.('ended', ended, { once: true });
    const entry: RenderEntry = {
      handle,
      publicationRef,
      track,
      attachments: new Map(),
      removeEndedListener: () => mediaTrack.removeEventListener?.('ended', ended),
    };
    this.entries.set(handle.handleId, entry);
    this.handleByTrack.set(track as object, handle.handleId);
    this.handleByMediaTrack.set(mediaTrack as object, handle.handleId);
    return handle;
  }

  attach(handle: SfuRemoteVideoHandle, target: HTMLVideoElement): SfuRelease {
    this.ensureOpen();
    const entry = this.resolve(handle);
    if (!entry) throw new Error('sfu_video_handle_stale');
    this.targets.get(target)?.();
    if (entry.attachments.size >= ATTACHMENTS_PER_HANDLE_MAX) {
      throw new Error('sfu_video_attachment_capacity_exceeded');
    }
    entry.track.attach(target);
    let active = true;
    const release = () => {
      if (!active) return;
      active = false;
      entry.attachments.delete(target);
      if (this.targets.get(target) === release) this.targets.delete(target);
      try { entry.track.detach(target); } catch { /* deterministic cleanup */ }
      if (target.srcObject !== null) target.srcObject = null;
    };
    entry.attachments.set(target, release);
    this.targets.set(target, release);
    return release;
  }

  matchesPublication(handle: SfuRemoteVideoHandle, publicationRef: string): boolean {
    return this.resolve(handle)?.publicationRef === publicationRef;
  }

  handleForPublication(publicationRef: string): SfuRemoteVideoHandle | null {
    return [...this.entries.values()].find(entry => entry.publicationRef === publicationRef)?.handle ?? null;
  }

  handleForMediaTrack(track: MediaStreamTrack): SfuRemoteVideoHandle | null {
    const id = this.handleByMediaTrack.get(track as object);
    return id ? this.entries.get(id)?.handle ?? null : null;
  }

  trackFor(handle: SfuRemoteVideoHandle): RemoteVideoTrack | null {
    return this.resolve(handle)?.track ?? null;
  }

  onUnavailable(
    callback: (value: Readonly<{ handleId: string; reason: SfuRemoteVideoUnavailableReason }>) => void,
  ): SfuRelease {
    this.ensureOpen();
    if (this.unavailableListeners.size >= UNAVAILABLE_LISTENERS_MAX) {
      throw new Error('sfu_video_listener_capacity_exceeded');
    }
    this.unavailableListeners.add(callback);
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      this.unavailableListeners.delete(callback);
    };
  }

  releaseTrack(track: object, reason: SfuRemoteVideoUnavailableReason): string | null {
    const handleId = this.handleByTrack.get(track);
    return handleId && this.releaseHandle(handleId, reason) ? handleId : null;
  }

  clear(): void {
    for (const handleId of [...this.entries.keys()]) this.releaseHandle(handleId, 'ended');
    this.targets.clear();
  }

  destroy(): void {
    if (this.destroyed) return;
    this.clear();
    this.unavailableListeners.clear();
    this.destroyed = true;
  }

  snapshot(): Readonly<{ handles: number; attachments: number; listeners: number }> {
    return Object.freeze({
      handles: this.entries.size,
      attachments: this.targets.size,
      listeners: this.unavailableListeners.size,
    });
  }

  private resolve(handle: SfuRemoteVideoHandle): RenderEntry | null {
    const entry = this.entries.get(handle.handleId);
    return entry?.handle === handle ? entry : null;
  }

  private releaseHandle(handleId: string, reason: SfuRemoteVideoUnavailableReason): boolean {
    const entry = this.entries.get(handleId);
    if (!entry) return false;
    this.entries.delete(handleId);
    this.handleByTrack.delete(entry.track as object);
    this.handleByMediaTrack.delete(entry.track.mediaStreamTrack as object);
    entry.removeEndedListener();
    for (const release of [...entry.attachments.values()].reverse()) release();
    entry.attachments.clear();
    const event = Object.freeze({ handleId, reason });
    for (const callback of [...this.unavailableListeners]) callback(event);
    return true;
  }

  private ensureOpen(): void {
    if (this.destroyed) throw new Error('sfu_video_registry_destroyed');
  }
}
