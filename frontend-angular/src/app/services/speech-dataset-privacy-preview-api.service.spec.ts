import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { HubApiCoreService } from './hub-api-core.service';
import {
  SpeechDatasetPrivacyPreviewApiService,
  parseSpeechDatasetPrivacyPreviewResponse,
} from './speech-dataset-privacy-preview-api.service';

const DIGEST = 'a'.repeat(64);
const RESPONSE = {
  ok: true,
  data: {
    schema: 'ananta.speech-dataset-privacy-preview.v1',
    dataset_id: 'dataset-one',
    manifest_digest: DIGEST,
    record_count: 3,
    total_duration_ms: 3_000,
    data_class_counts: { audio: 3, transcript: 3 },
    contributor_scopes: { ['b'.repeat(64)]: 3 },
    grant_ref_counts: { 'consent-one': 3 },
    quarantine_count: 1,
    scan_findings: { pii: 1 },
    raw_audio_preview: { authorized: false, refs: [] },
  },
};

describe('SpeechDatasetPrivacyPreviewApiService', () => {
  const core = { request: vi.fn(() => of(RESPONSE)) };

  beforeEach(() => {
    core.request.mockClear();
    TestBed.configureTestingModule({
      providers: [
        SpeechDatasetPrivacyPreviewApiService,
        { provide: HubApiCoreService, useValue: core },
      ],
    });
  });

  it('loads aggregate preview without granting raw audio', () => {
    TestBed.inject(SpeechDatasetPrivacyPreviewApiService)
      .aggregate('http://hub.test/', DIGEST).subscribe(preview => expect(preview.recordCount).toBe(3));
    expect(core.request).toHaveBeenCalledWith(
      'GET',
      `http://hub.test/v1/semantic-media/privacy/speech-datasets/${DIGEST}/preview`,
      'http://hub.test',
      { headers: undefined },
    );
  });

  it('sends a separate bounded preview grant only for explicit raw preview', () => {
    TestBed.inject(SpeechDatasetPrivacyPreviewApiService)
      .withRawAudioGrant('http://hub.test', DIGEST, 'preview-grant-one').subscribe();
    expect(core.request.mock.calls[0][3]).toEqual({
      headers: { 'X-Speech-Preview-Grant': 'preview-grant-one' },
    });
    expect(core.request.mock.calls[0][1]).toContain('?include_raw_audio=true');
  });

  it('rejects unknown fields and unauthorized raw refs', () => {
    expect(() => parseSpeechDatasetPrivacyPreviewResponse({
      ...RESPONSE,
      data: { ...RESPONSE.data, unknown: true },
    })).toThrow('speech_preview_response_invalid');
    expect(() => parseSpeechDatasetPrivacyPreviewResponse({
      ...RESPONSE,
      data: { ...RESPONSE.data, raw_audio_preview: { authorized: false, refs: ['artifact://speech-preview/a'] } },
    })).toThrow('speech_preview_raw_invalid');
  });
});
