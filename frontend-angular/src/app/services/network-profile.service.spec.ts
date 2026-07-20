import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import {
  NetworkProfileService,
  SEMANTIC_MEDIA_FEATURE_DEFAULTS,
  normalizeIceServers,
  normalizeSemanticMediaFeatureFlags,
} from './network-profile.service';

describe('NetworkProfileService semantic media flags', () => {
  it('drops credential-less TURN entries before constructing RTCPeerConnection', () => {
    expect(normalizeIceServers([
      { urls: 'turn:webrtc.invalid:3478' },
      { urls: ['stun:webrtc.invalid:3478', 'turns:webrtc.invalid:5349'] },
      { urls: 'turn:webrtc.invalid:3478', username: 'peer', credential: 'secret' },
    ])).toEqual([
      { urls: ['stun:webrtc.invalid:3478'] },
      { urls: 'turn:webrtc.invalid:3478', username: 'peer', credential: 'secret' },
    ]);
  });

  it('fails closed for missing, malformed and dependency-inconsistent values', () => {
    expect(normalizeSemanticMediaFeatureFlags(undefined)).toEqual(SEMANTIC_MEDIA_FEATURE_DEFAULTS);
    expect(normalizeSemanticMediaFeatureFlags({ semantic_visual_capture: 'true' }))
      .toEqual(SEMANTIC_MEDIA_FEATURE_DEFAULTS);
    expect(normalizeSemanticMediaFeatureFlags({ speech_adapter_routing: true }).speech_adapter_routing)
      .toBe(false);
  });

  it('retains the all-false fallback when the Hub cannot be reached', async () => {
    TestBed.configureTestingModule({
      providers: [
        NetworkProfileService,
        { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
        { provide: HubApiCoreService, useValue: { get: () => throwError(() => new Error('offline')) } },
      ],
    });
    const service = TestBed.inject(NetworkProfileService);
    await service.load();
    expect(service.current.semantic_media_feature_flags).toEqual(SEMANTIC_MEDIA_FEATURE_DEFAULTS);
  });

  it('accepts only a dependency-complete Hub projection', async () => {
    const profile = {
      profile_id: 'public-ananta', label: 'Test', oidc: {
        issuer: '', client_id: '', audience: '', pkce_required: true,
      },
      rendezvous: { base_url: '', signaling_url: '', transport_order: ['hub_relay'] },
      ice_servers: [], require_e2e_payload_encryption: true, signaling_url: '',
      transport_order: ['hub_relay'], warning: '',
      semantic_media_feature_flags: {
        ...SEMANTIC_MEDIA_FEATURE_DEFAULTS,
        semantic_speech_runtime: true,
        semantic_media_background_operations: true,
        peer_evidence_sync: true,
      },
    };
    TestBed.configureTestingModule({
      providers: [
        NetworkProfileService,
        { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
        { provide: HubApiCoreService, useValue: { get: () => of({ ok: true, profile }) } },
      ],
    });
    const service = TestBed.inject(NetworkProfileService);
    await service.load();
    expect(service.current.semantic_media_feature_flags.peer_evidence_sync).toBe(true);
    expect(service.current.semantic_media_feature_flags.speech_reconciliation).toBe(false);
  });
});
