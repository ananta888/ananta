import type { RemoteVideoTrack } from 'livekit-client';
import { describe, expect, it, vi } from 'vitest';

import { LivekitSfuVideoRenderAdapter } from './livekit-sfu-video-render.adapter';

function remoteVideo() {
  const mediaStreamTrack = new EventTarget() as MediaStreamTrack;
  const track = {
    mediaStreamTrack,
    attach: vi.fn(),
    detach: vi.fn(),
  } as unknown as RemoteVideoTrack;
  return { track, mediaStreamTrack };
}

describe('LivekitSfuVideoRenderAdapter', () => {
  it('uses official attach/detach with an opaque identity-bound handle', () => {
    const adapter = new LivekitSfuVideoRenderAdapter();
    const remote = remoteVideo();
    const handle = adapter.register(remote.track, 'publication-private', 'camera')!;
    const target = document.createElement('video');

    expect(handle).toEqual({ handleId: 'sfu-video-1', source: 'camera' });
    expect(JSON.stringify(handle)).not.toMatch(/publication|participant|publisher|track-private/i);
    const release = adapter.attach(handle, target);
    expect(remote.track.attach).toHaveBeenCalledWith(target);
    expect(() => adapter.attach({ ...handle }, target)).toThrow('sfu_video_handle_stale');
    release();
    release();
    expect(remote.track.detach).toHaveBeenCalledOnce();
    expect(adapter.snapshot()).toEqual({ handles: 1, attachments: 0, listeners: 0 });
  });

  it('releases every target and SDK reference on unsubscribe, ended and destroy', () => {
    const adapter = new LivekitSfuVideoRenderAdapter(2);
    const unavailable = vi.fn();
    adapter.onUnavailable(unavailable);
    const first = remoteVideo();
    const firstHandle = adapter.register(first.track, 'publication-a', 'camera')!;
    adapter.attach(firstHandle, document.createElement('video'));
    expect(adapter.releaseTrack(first.track as object, 'unsubscribed')).toBe(firstHandle.handleId);
    expect(unavailable).toHaveBeenLastCalledWith({ handleId: firstHandle.handleId, reason: 'unsubscribed' });

    const second = remoteVideo();
    const secondHandle = adapter.register(second.track, 'publication-b', 'screen')!;
    adapter.attach(secondHandle, document.createElement('video'));
    second.mediaStreamTrack.dispatchEvent(new Event('ended'));
    expect(unavailable).toHaveBeenLastCalledWith({ handleId: secondHandle.handleId, reason: 'ended' });
    adapter.destroy();
    adapter.destroy();
    expect(adapter.snapshot()).toEqual({ handles: 0, attachments: 0, listeners: 0 });
  });

  it('bounds the registry instead of evicting an active receiver', () => {
    const adapter = new LivekitSfuVideoRenderAdapter(1);
    expect(adapter.register(remoteVideo().track, 'publication-a', 'camera')).not.toBeNull();
    expect(adapter.register(remoteVideo().track, 'publication-b', 'camera')).toBeNull();
    expect(adapter.snapshot().handles).toBe(1);
  });
});
