import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import {
  SpeechAdapterRegistryApiService,
  parseSpeechAdapterRegistryPage,
} from './speech-adapter-registry-api.service';

function row(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    adapter_id: 'speech-adapter-a', version: 'v1', pair_id: 'pair-a', direction: 'sender_to_receiver',
    speaker_digest: 'a'.repeat(64), scope_digest: 'b'.repeat(64), base_model_id: 'base-a',
    base_model_digest: 'c'.repeat(64), backend: 'mock', backend_digest: 'd'.repeat(64),
    dataset_digest: 'e'.repeat(64), split_digest: 'f'.repeat(64), evaluation_report_digest: '1'.repeat(64),
    evaluation_policy_version: 'speech-eval-policy.v1', consent_digest: '2'.repeat(64),
    consent_expires_at_ms: 2_000, artifact_ref: 'artifact://speech-adapters/a/speech-adapter-a',
    artifact_sha256: '3'.repeat(64), artifact_size_bytes: 128, expires_at_ms: 1_900,
    status: 'approved', registry_version: 2, approval_reason_code: 'manual', approved_at_ms: 1_000,
    revoked_at_ms: null, deprecated_at_ms: null, expired_at_ms: null, rollback_of_adapter_id: null,
    created_at_ms: 900, updated_at_ms: 1_000, lineage: [], ...overrides,
  };
}

describe('SpeechAdapterRegistryApiService', () => {
  it('requests only the explicit pair and direction and returns a content-free metadata subset', () => {
    const request = vi.fn(() => of({ ok: true, data: { items: [row()], count: 1 } }));
    TestBed.configureTestingModule({ providers: [
      SpeechAdapterRegistryApiService,
      { provide: HubApiCoreService, useValue: { request } },
    ] });
    const service = TestBed.inject(SpeechAdapterRegistryApiService);
    service.list('http://hub.test/', 'pair-a', 'sender_to_receiver').subscribe(page => {
      expect(page.items[0]).toEqual({
        adapter_id: 'speech-adapter-a', pair_id: 'pair-a', direction: 'sender_to_receiver',
        speaker_digest: 'a'.repeat(64), scope_digest: 'b'.repeat(64), base_model_id: 'base-a',
        base_model_digest: 'c'.repeat(64), consent_digest: '2'.repeat(64),
        artifact_ref: 'artifact://speech-adapters/a/speech-adapter-a', artifact_sha256: '3'.repeat(64),
        expires_at_ms: 1_900, consent_expires_at_ms: 2_000, registry_version: 2, status: 'approved',
      });
      expect(JSON.stringify(page)).not.toContain('storage_ref');
      expect(JSON.stringify(page)).not.toContain('server_path');
      expect(JSON.stringify(page)).not.toContain('key');
    });
    expect(request).toHaveBeenCalledWith(
      'GET',
      'http://hub.test/api/ml-intern-speech-adapters?pair_id=pair-a&direction=sender_to_receiver',
      'http://hub.test',
    );
  });

  it('rejects server paths, unknown fields and unapproved status aliases', () => {
    expect(() => parseSpeechAdapterRegistryPage({
      ok: true, data: { items: [row({ storage_ref: '/srv/private' })], count: 1 },
    })).toThrow('speech_adapter_metadata_invalid');
    expect(() => parseSpeechAdapterRegistryPage({
      ok: true, data: { items: [row({ status: 'active' })], count: 1 },
    })).toThrow('speech_adapter_status_invalid');
    expect(() => parseSpeechAdapterRegistryPage({
      ok: true, data: { items: [row({ artifact_ref: '/srv/private' })], count: 1 },
    })).toThrow('speech_adapter_artifact_ref_invalid');
  });
});
