import { describe, expect, it, vi } from 'vitest';

import type { ValidatedSfuBroadcastContract } from './sfu-broadcast-contracts';
import { SfuBroadcastPublicationControllerService } from './sfu-broadcast-publication-controller.service';
import type { SfuPublicationPort, SfuPublishedTrack } from './sfu-room-session.ports';

describe('SfuBroadcastPublicationControllerService', () => {
  it('accepts only a validated projection, preserves epochs and keeps one publication per source', async () => {
    const controller = new SfuBroadcastPublicationControllerService();
    const port = fakePort();
    const firstTrack = track();
    const first = await controller.apply(port, contract(1), firstTrack);
    expect(port.publishProjected).toHaveBeenCalledWith(expect.objectContaining({
      validation: 'hub-contract-accepted-v1', projectionVersion: 1, routeEpoch: 7, keyEpoch: 5,
    }), firstTrack);
    expect(first.observation).toEqual({
      status: 'unsupported', codecClass: null, simulcasted: null,
      activeEncodingCount: null, observedRidCount: null, scalabilityClass: null,
    });

    const secondTrack = track();
    await controller.apply(port, contract(2), secondTrack);
    expect(port.unpublish).toHaveBeenCalledWith(first);
    await expect(controller.apply(port, contract(2), track())).rejects.toThrow('sfu_publisher_projection_replay');
    await controller.stopAll(port);
    expect(secondTrack.stop).toHaveBeenCalledOnce();
  });

  it('does not turn requested encodings into positive observed claims', async () => {
    const controller = new SfuBroadcastPublicationControllerService();
    const publication = await controller.apply(fakePort(), contract(1), track());
    expect(publication.observation?.status).toBe('unsupported');
    expect(publication.observation?.codecClass).toBeNull();
    expect(publication.observation?.activeEncodingCount).toBeNull();
  });
});

function fakePort(): SfuPublicationPort & Record<string, any> {
  const publications = new Map<string, SfuPublishedTrack>();
  return {
    publish: vi.fn(),
    publishProjected: vi.fn(async (projection, mediaTrack) => {
      const value: SfuPublishedTrack = {
        publicationId: projection.publicationId, trackSid: `TR_${projection.projectionVersion}`,
        track: mediaTrack, projectionVersion: projection.projectionVersion,
        routeEpoch: projection.routeEpoch, keyEpoch: projection.keyEpoch,
        observation: {
          status: 'unsupported', codecClass: null, simulcasted: null,
          activeEncodingCount: null, observedRidCount: null, scalabilityClass: null,
        },
      };
      publications.set(value.publicationId, value);
      return value;
    }),
    unpublish: vi.fn(async publication => {
      publications.delete(publication.publicationId);
      publication.track.stop();
    }),
    denySubscriptionsByDefault: vi.fn(),
    setTrackAudience: vi.fn(),
  };
}

function track(): MediaStreamTrack {
  return { kind: 'video', stop: vi.fn(), onended: null } as unknown as MediaStreamTrack;
}

function contract(version: number) {
  return {
    contractId: 'ananta.sfu-publisher-layer-projection.v1',
    document: {
      media_kind: 'video', publication_ref: 'camera-a', resolution: 'planned', safe_outcome: 'apply_projection',
      projection_version: version, route_epoch: 7, key_epoch: 5,
      encoding_plan: [{
        encoding_class: 'video_baseline', codec_class: 'video_vp8', rid_class: 'high',
        scalability_class: 'simulcast', spatial_id: 0, temporal_id_max: 2,
        max_bitrate_bps: 900000, max_width: 1280, max_height: 720, max_fps: 30,
      }],
    },
  } as unknown as Extract<ValidatedSfuBroadcastContract, {
    contractId: 'ananta.sfu-publisher-layer-projection.v1';
  }>;
}
