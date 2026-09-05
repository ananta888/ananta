import {
  PEER_OVERLAY_PRIORITY,
  PEER_OVERLAY_TRAFFIC_PROFILES,
  requirePeerOverlayDataClass,
} from './peer-overlay-traffic-policy';

describe('peer overlay traffic policy', () => {
  it('reserves bounded fail-closed lanes for control and rekey', () => {
    expect(PEER_OVERLAY_PRIORITY.slice(0, 2)).toEqual(['control', 'rekey']);
    expect(PEER_OVERLAY_TRAFFIC_PROFILES.control).toMatchObject({
      deliveryMode: 'reliable', ordering: 'ordered', overload: 'fail_closed',
    });
    expect(PEER_OVERLAY_TRAFFIC_PROFILES.rekey.queueBytes).toBeGreaterThan(0);
    expect(PEER_OVERLAY_TRAFFIC_PROFILES.bulk.queueMessages)
      .toBeLessThan(PEER_OVERLAY_TRAFFIC_PROFILES.control.queueMessages);
  });

  it('rejects unknown classes and media forwarding forbidden by DG-01', () => {
    expect(() => requirePeerOverlayDataClass('invented')).toThrow('peer_overlay_traffic_class_unknown');
    expect(() => requirePeerOverlayDataClass('audio')).toThrow('peer_overlay_media_class_forbidden');
    expect(() => requirePeerOverlayDataClass('video_keyframe')).toThrow('peer_overlay_media_class_forbidden');
  });
});
