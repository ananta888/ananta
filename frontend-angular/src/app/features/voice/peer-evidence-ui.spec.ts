import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { of } from 'rxjs';

import { SpeechDatasetLineageComponent } from '../ml-intern/speech-dataset-lineage.component';
import { SpeechDatasetPrivacyPreviewComponent } from '../ml-intern/speech-dataset-privacy-preview.component';
import { SpeechDatasetPrivacyPreviewApiService } from '../../services/speech-dataset-privacy-preview-api.service';
import previewFixture from '../../services/fixtures/speech-evidence-offer-preview.v2.json';
import { speechEvidenceGroupPreviews } from '../../services/speech-evidence-sync.validators';
import { PeerEvidenceSyncPanelComponent } from './peer-evidence-sync-panel.component';
import { PeerTranscriptConflictPanelComponent } from './peer-transcript-conflict-panel.component';

describe('peer speech evidence UI', () => {
  it('shows both originals and keeps display override explicitly local', async () => {
    await TestBed.configureTestingModule({ imports: [PeerTranscriptConflictPanelComponent] }).compileComponents();
    const fixture = TestBed.createComponent(PeerTranscriptConflictPanelComponent);
    fixture.componentInstance.candidates = [
      { candidateId: 'a', contributorLabel: 'Lokal', sourceLabel: 'ASR A', revision: 1, text: 'Original A', verified: true },
      { candidateId: 'b', contributorLabel: 'Peer', sourceLabel: 'ASR B', revision: 1, text: 'Original B', verified: true },
    ];
    fixture.componentInstance.regions = [
      { regionId: 'r1', kind: 'lexical', candidateIds: ['a', 'b'], selectedCandidateId: null, unresolved: true, reasonCode: 'quarantined' },
    ];
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Original A');
    expect(fixture.nativeElement.textContent).toContain('Original B');
    expect(fixture.nativeElement.textContent).toContain('keine stille Ergänzung');
    expect(fixture.nativeElement.textContent).toContain('weder Evidence, Receipt noch Dataset');
  });

  it('shows scope before consent and blocks raw-audio bulk acceptance', async () => {
    await TestBed.configureTestingModule({ imports: [PeerEvidenceSyncPanelComponent] }).compileComponents();
    const fixture: ComponentFixture<PeerEvidenceSyncPanelComponent> = TestBed.createComponent(PeerEvidenceSyncPanelComponent);
    fixture.componentInstance.offer = {
      offerId: 'offer', direction: 'sender_to_receiver', purpose: 'speech_dataset_curation',
      dataClasses: ['text_corrections', 'raw_audio'], fields: ['transcript'], retentionSeconds: 3600,
      trainerClass: 'speech_adaptation', groupCount: 1, totalBytes: 3, senderConsentVersion: 3,
      recipientConsentVersion: 4, state: 'proposed', action: 'accept', expiresAtMs: Date.now() + 60_000,
      groupPreviews: speechEvidenceGroupPreviews({ group_previews: [previewFixture.valid] }), previewVerified: true,
    };
    fixture.componentInstance.sync = {
      state: 'paused', acknowledgedChunks: 1, chunkCount: 2, firstMissingIndex: 1,
      inFlightBytes: 0, retries: 1, quarantineCount: 1, receiptId: 'receipt-test', revocationState: 'unresolved',
      pending: false, receiptVerification: 'peer_verified', reasonCode: null, localGroups: [], quarantine: [],
      curationTaskId: null, datasetId: null, datasetManifestDigest: null, datasetLineageNodes: [],
      lineage: [], candidates: [], regions: [], resolutionHash: '', resolutionPolicyVersion: '',
    };
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('Richtung'); expect(text).toContain('Retention'); expect(text).toContain('Trainerklasse');
    expect(text).toContain('Inhaltsfreie Originalkandidaten');
    expect(text).toContain('Original 1');
    expect(text).toContain('Original 2');
    expect(text).toContain('Resolution: resolved');
    expect(text).toContain('weder Rohtext noch Audio oder Features');
    expect(text).toContain('raw_audio – kein Bulk-Accept');
    expect(text).toContain('keine Löschung wird behauptet');
  });

  it('mounts the privacy preview only after Hub curation exposes a manifest digest', async () => {
    const digest = 'a'.repeat(64);
    const aggregate = vi.fn(() => of({
      schema: 'ananta.speech-dataset-privacy-preview.v1' as const,
      datasetId: 'dataset-a', manifestDigest: digest, recordCount: 2, totalDurationMs: 1000,
      dataClassCounts: {}, contributorScopes: {}, grantRefCounts: {}, quarantineCount: 0,
      scanFindings: {}, rawAudioPreview: { authorized: false, refs: [] },
    }));
    await TestBed.configureTestingModule({
      imports: [PeerEvidenceSyncPanelComponent],
      providers: [{
        provide: SpeechDatasetPrivacyPreviewApiService,
        useValue: { aggregate, withRawAudioGrant: vi.fn() },
      }],
    }).compileComponents();
    const fixture = TestBed.createComponent(PeerEvidenceSyncPanelComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    const sync = {
      state: 'completed', pending: false, acknowledgedChunks: 1, chunkCount: 1, firstMissingIndex: 1,
      inFlightBytes: 0, retries: 0, quarantineCount: 0, receiptId: 'receipt-a',
      receiptVerification: 'hub_verified' as const, curationTaskId: 'task-a', datasetId: 'dataset-a',
      datasetManifestDigest: null, datasetLineageNodes: [],
      revocationState: null, reasonCode: null, localGroups: [], quarantine: [],
      lineage: [], candidates: [], regions: [], resolutionHash: '', resolutionPolicyVersion: '',
    };
    fixture.componentRef.setInput('sync', sync);
    fixture.detectChanges();
    expect(fixture.debugElement.query(By.directive(SpeechDatasetPrivacyPreviewComponent))).toBeNull();
    expect(fixture.debugElement.query(By.directive(SpeechDatasetLineageComponent))).toBeNull();
    expect(aggregate).not.toHaveBeenCalled();

    fixture.componentRef.setInput('sync', {
      ...sync,
      datasetManifestDigest: digest,
      datasetLineageNodes: [{
        datasetId: 'dataset-a', version: `sha256:${digest}`, parentVersion: null,
        manifestDigest: digest, receiptId: 'b'.repeat(64), contributorDigests: ['c'.repeat(64)],
        direction: 'sender_to_receiver', consentDigest: 'd'.repeat(64),
        fieldProvenanceDigests: ['e'.repeat(64)], createdByTaskId: 'task-a',
      }],
    });
    fixture.detectChanges();
    expect(fixture.debugElement.query(By.directive(SpeechDatasetPrivacyPreviewComponent))).not.toBeNull();
    expect(fixture.debugElement.query(By.directive(SpeechDatasetLineageComponent))).not.toBeNull();
    expect(fixture.nativeElement.textContent).toContain('Hub-Task task-a');
    expect(aggregate).toHaveBeenCalledWith('http://hub.test', digest);
  });

  it('renders immutable parent and contributor lineage', async () => {
    await TestBed.configureTestingModule({ imports: [SpeechDatasetLineageComponent] }).compileComponents();
    const fixture = TestBed.createComponent(SpeechDatasetLineageComponent);
    fixture.componentInstance.nodes = [{
      datasetId: 'speech-dataset', version: 'v2', parentVersion: 'v1', manifestDigest: 'manifest',
      receiptId: 'receipt', contributorDigests: ['contributor'], direction: 'sender_to_receiver',
      consentDigest: 'consent', fieldProvenanceDigests: ['fields'], createdByTaskId: 'hub-task',
    }];
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Parent v1');
    expect(fixture.nativeElement.textContent).toContain('Hub-Task hub-task');
    expect(fixture.nativeElement.textContent).toContain('keine Split-, Training- oder Adapterlogik');
  });
});
