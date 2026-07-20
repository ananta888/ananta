import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SpeechDatasetPrivacyPreviewApiService } from '../../services/speech-dataset-privacy-preview-api.service';
import { SpeechDatasetPrivacyPreviewComponent } from './speech-dataset-privacy-preview.component';

const DIGEST = 'a'.repeat(64);
const preview = {
  schema: 'ananta.speech-dataset-privacy-preview.v1' as const,
  datasetId: 'dataset-one', manifestDigest: DIGEST, recordCount: 3, totalDurationMs: 3_000,
  dataClassCounts: { audio: 3 }, contributorScopes: { ['b'.repeat(64)]: 3 },
  grantRefCounts: { 'consent-one': 3 }, quarantineCount: 1, scanFindings: { pii: 1 },
  rawAudioPreview: { authorized: false, refs: [] as readonly string[] },
};

describe('SpeechDatasetPrivacyPreviewComponent', () => {
  const api = {
    aggregate: vi.fn(() => of(preview)),
    withRawAudioGrant: vi.fn(() => of({
      ...preview,
      rawAudioPreview: { authorized: true, refs: ['artifact://speech-preview/item-one'] },
    })),
  };
  let fixture: ComponentFixture<SpeechDatasetPrivacyPreviewComponent>;

  beforeEach(async () => {
    api.aggregate.mockClear();
    api.withRawAudioGrant.mockClear();
    await TestBed.configureTestingModule({
      imports: [SpeechDatasetPrivacyPreviewComponent],
      providers: [{ provide: SpeechDatasetPrivacyPreviewApiService, useValue: api }],
    }).compileComponents();
    fixture = TestBed.createComponent(SpeechDatasetPrivacyPreviewComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('manifestDigest', DIGEST);
    fixture.detectChanges();
  });

  it('loads and renders only aggregate data by default', () => {
    expect(api.aggregate).toHaveBeenCalledWith('http://hub.test', DIGEST);
    expect(api.withRawAudioGrant).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('Records');
    expect(fixture.nativeElement.textContent).toContain('Raw-Audio-Vorschau nicht freigegeben');
  });

  it('requests raw refs only after an explicit separate grant', () => {
    const component = fixture.componentInstance;
    component.rawPreviewGrant = 'preview-grant-one';
    component.requestRawPreview();
    fixture.detectChanges();
    expect(api.withRawAudioGrant).toHaveBeenCalledWith('http://hub.test', DIGEST, 'preview-grant-one');
    expect(component.rawPreviewGrant).toBe('');
    expect(fixture.nativeElement.textContent).toContain('1 begrenzte Referenzen');
  });
});
