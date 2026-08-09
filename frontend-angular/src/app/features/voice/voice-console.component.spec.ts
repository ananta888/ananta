import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { Subject, of, throwError } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  VOICE_AUDIO_CAPTURE,
  VOICE_BATCH_RECORDING,
  VoiceAudioCapturePort,
  VoiceBatchRecordingPort,
} from './voice-audio-capture';
import { VoiceApiService } from './voice-api.service';
import { VoiceConsoleComponent } from './voice-console.component';
import {
  SemanticMediaProgramFacade,
  SemanticMediaProgramHostView,
} from './semantic-media-program.facade';
import { SemanticMediaProgramHostComponent } from './semantic-media-program-host.component';
import { VOICE_LONG_RUN_RECOVERY, VoiceLongRunRecoveryMetadata } from './voice-long-run-recovery';
import { VOICE_LONG_RUN_SPOOL } from './voice-long-run-spool';
import { VoiceLongRunTimelineSegment } from './voice-long-run-timeline';

beforeAll(async () => {
  await ɵresolveComponentResources((resource) => {
    const file = path.basename(String(resource));
    const directory = file.startsWith('pair-compute-') ? 'pair-view' : 'voice';
    return readFile(path.resolve(process.cwd(), 'src/app/features', directory, file), 'utf8');
  });
});

describe('VoiceConsoleComponent', () => {
  let capturePrepared = false;
  const api = {
    getCapabilities: vi.fn(),
    getConfigurationSchema: vi.fn(),
    getConfiguration: vi.fn(),
    saveConfiguration: vi.fn(),
    createStream: vi.fn(),
    pushStreamChunk: vi.fn(),
    finalizeStream: vi.fn(),
    cancelStream: vi.fn(),
    acquireLongRunLease: vi.fn(),
    createLongRun: vi.fn(),
    getLongRun: vi.fn(),
    heartbeatLongRun: vi.fn(),
    uploadLongRunSegment: vi.fn(),
    stopLongRun: vi.fn(),
    transcribe: vi.fn(),
    createReview: vi.fn(),
    decideReview: vi.fn(),
  };
  const capture: VoiceAudioCapturePort = {
    supported: true,
    active: false,
    get prepared() { return capturePrepared; },
    supportsSource: vi.fn(() => true),
    prepare: vi.fn(async () => { capturePrepared = true; }),
    start: vi.fn(),
    stop: vi.fn(async () => { capturePrepared = false; }),
  };
  const batch: VoiceBatchRecordingPort = {
    supported: true,
    active: false,
    supportsSource: vi.fn(() => true),
    start: vi.fn(),
    stop: vi.fn(),
    cancel: vi.fn(),
  };
  let recoveryValue: VoiceLongRunRecoveryMetadata | null = null;
  const recovery = {
    load: vi.fn(() => recoveryValue),
    save: vi.fn((value: VoiceLongRunRecoveryMetadata) => { recoveryValue = value; }),
    clear: vi.fn(() => { recoveryValue = null; }),
  };
  const spool = {
    initialize: vi.fn(async () => undefined),
    put: vi.fn(),
    read: vi.fn(),
    list: vi.fn(async () => []),
    delete: vi.fn(async () => undefined),
    clearRun: vi.fn(async () => undefined),
    signalProfileDeletion: vi.fn(),
    clearProfile: vi.fn(async () => undefined),
    allowProfile: vi.fn(async () => Date.now() || 1),
    stats: vi.fn(async () => ({ segments: 0, bytes: 0, maxSegments: 5, maxBytes: 24 * 1024 * 1024 })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    capturePrepared = false;
    recoveryValue = null;
    api.getCapabilities.mockReturnValue(of({
      available: true,
      provider: 'voice-runtime',
      capabilities: ['streaming', 'generative_rewrite'],
      models: [{ id: 'vosk-de', backend: 'vosk', available: true }],
      correction_models: [
        { id: 'gemma-3-4b-it', backend: 'generative_corrector', available: true, revision: 'r1' },
        { id: 'phi-4-mini', backend: 'generative_corrector', available: true, revision: 'r2' },
      ],
    }));
    api.getConfigurationSchema.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      fields: [
        { key: 'recognition_strategy', type: 'enum', enum: ['single'] },
        { key: 'primary_backend', type: 'enum', enum: ['vosk'] },
        { key: 'generative_corrector_model', type: 'enum', enum: ['gemma-3-4b-it', 'phi-4-mini'] },
      ],
    }));
    api.getConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        recognition_strategy: 'single',
        primary_backend: 'vosk',
        correction_policy: 'generative_rewrite',
        generative_corrector_model: 'gemma-3-4b-it',
        feature_flags: { generative_corrector: true },
      },
      sources: [],
      version: 1,
    }));
    api.saveConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      scope: 'profile', scope_id: 'default', delta: {}, version: 2,
    }));
    api.acquireLongRunLease.mockReturnValue(of({
      lease_token: 'lease-a', expires_at: Date.now() + 60_000, profile_id: 'default',
    }));
    api.createLongRun.mockReturnValue(of({
      run: { id: 'voice-long-run-a', status: 'active' },
      segments: [], gaps: [], composed_transcript: null,
      resume: { next_sequence: 0, acknowledged_through_sequence: -1, last_seen_sequence: -1 },
    }));
    api.getLongRun.mockReturnValue(of({
      run: { id: 'voice-long-run-a', status: 'active' },
      segments: [], gaps: [], composed_transcript: null,
      resume: { next_sequence: 0, acknowledged_through_sequence: -1, last_seen_sequence: -1 },
    }));
    api.heartbeatLongRun.mockReturnValue(of({
      run: { id: 'voice-long-run-a', status: 'active' },
      segments: [], gaps: [], composed_transcript: null,
      resume: { next_sequence: 0, acknowledged_through_sequence: -1, last_seen_sequence: -1 },
    }));
    api.uploadLongRunSegment.mockImplementation(
      (_hub: string, runId: string, sequence: number) => of({
        run: { id: runId, status: 'active' },
        segment: { sequence, status: 'completed', text: null },
        result: { text: `segment-${sequence}` },
        segments: [], gaps: [], composed_transcript: null,
        resume: { next_sequence: sequence + 1, acknowledged_through_sequence: sequence },
      }),
    );
    api.stopLongRun.mockReturnValue(of({
      run: { id: 'voice-long-run-a', status: 'completed' },
      segments: [], gaps: [], composed_transcript: 'Fertiges Transkript',
      resume: { next_sequence: 0, acknowledged_through_sequence: -1 },
    }));

    const programView: SemanticMediaProgramHostView = {
        scope: {
          direction: 'bidirectional', dataClass: 'Transcript', purpose: 'Test', retentionLabel: 'keine',
          trainerLocation: 'lokal', e2eeMode: 'strict_e2ee', ordinaryFallback: 'aktiv',
        },
        capabilities: [], online: true, hubUrl: 'http://hub.test',
        ordinaryMediaAuthority: 'hub', ordinaryMediaActivationEnabled: true, computeVisible: false,
        compute: {
          contract: { contractId: '', revision: 0, status: 'absent', profile: 'off', delayMs: 5_000, roles: {} },
          leases: [], pending: false, errorCode: null,
        },
        receiverPaths: [],
        ordinaryMediaCaptureEnabled: false,
        ordinaryMediaVideoCaptureEnabled: false,
        ordinaryMediaReason: 'ordinary_media_not_active',
        ordinaryAudioState: {
          status: 'idle', trackId: null, deviceLabelVisible: false, reasonCode: null,
        },
        ordinaryMediaPublications: [],
        speechTransportState: 'stopped', speechTransportReason: 'not_started',
        speechTransportCanStart: false,
        speechSettings: {
          displayMode: 'live', segmentDurationSeconds: 60, correctEachSegment: true,
          paused: false, ordinaryAudioOverride: false,
        },
        speechQuality: {
          mode: 'ordinary_audio', reasonCode: 'quality_initial', transitioned: false,
          ordinaryAudioAvailable: true, liveTranscriptEnabled: true,
          delayedSourceEnabled: false, semanticFeaturesEnabled: false,
        },
        speechReconciliationHubAuthorized: false,
        evidenceOffer: null, evidenceSync: null, evidenceAvailableReason: 'none',
        evidenceConsent: {
          bound: false, signerIds: [], consent: null, pending: false,
          errorCode: 'speech_consent_context_missing',
        },
      };
    const programFacade = {
      view$: of(programView),
      start: vi.fn(async () => undefined), handleProgramIntent: vi.fn(), handleComputeIntent: vi.fn(),
      requestComputeSuggestion: vi.fn(), handleReceiverPathIntent: vi.fn(),
      startSpeech: vi.fn(), stopSpeech: vi.fn(), handleSpeechSettings: vi.fn(),
      handleEvidencePropose: vi.fn(), handleEvidenceAccept: vi.fn(), pauseEvidence: vi.fn(),
      resumeEvidence: vi.fn(), rejectEvidence: vi.fn(), revokeEvidence: vi.fn(),
      requestEvidenceCuration: vi.fn(), handleEvidenceLocalOverride: vi.fn(),
      handleEvidenceConsentIntent: vi.fn(),
      startOrdinaryMicrophone: vi.fn(), stopOrdinaryMicrophone: vi.fn(),
      setOrdinaryMicrophoneMuted: vi.fn(), startOrdinaryVideo: vi.fn(), stopOrdinaryVideo: vi.fn(),
      replaceOrdinaryVideo: vi.fn(), setOrdinaryVideoMuted: vi.fn(),
    };
    TestBed.overrideComponent(SemanticMediaProgramHostComponent, {
      set: { providers: [{ provide: SemanticMediaProgramFacade, useValue: programFacade }] },
    });
    TestBed.configureTestingModule({
      imports: [VoiceConsoleComponent],
      providers: [
        provideRouter([]),
        { provide: VoiceApiService, useValue: api },
        { provide: AgentDirectoryService, useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'http://hub.test' }] } },
        { provide: VOICE_AUDIO_CAPTURE, useValue: capture },
        { provide: VOICE_BATCH_RECORDING, useValue: batch },
        { provide: VOICE_LONG_RUN_SPOOL, useValue: spool },
        { provide: VOICE_LONG_RUN_RECOVERY, useValue: recovery },
      ],
    });
  });

  it('shows live and batch modes with Hub-provided correction models', () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;

    expect(text).toContain('Live-Transkription');
    expect(text).toContain('Langzeit bis 8 h');
    expect(text).toContain('Audioquelle');
    expect(text).toContain('Lautsprecher / Systemaudio');
    expect(text).toContain('gemma-3-4b-it');
    expect(text).toContain('phi-4-mini');
    expect(fixture.componentInstance.selectedBackend).toBe('vosk');
    expect(fixture.componentInstance.selectedCorrectorProvider).toBe('embedded');
    expect(fixture.componentInstance.selectedCorrectorModel).toBe('gemma-3-4b-it');
    expect((fixture.nativeElement as HTMLElement).querySelector('a[href="/settings?section=voice"]')).toBeTruthy();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="semantic-media-program"]')).toBeTruthy();
  });

  it('warns about the lower-accuracy Vosk-only long-run path without blocking capture', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    clickLongRunTab(fixture.nativeElement as HTMLElement);
    fixture.detectChanges();

    const hint = (fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-vosk-quality-hint"]');
    expect(hint?.textContent).toContain('ressourcensparend, aber weniger genau');
    expect(hint?.textContent).toContain('whisper_cpp');
    expect(hint?.textContent).toContain('LLM-Korrektur ersetzt keine bessere ASR-Erkennung');
    const start = [...(fixture.nativeElement as HTMLElement).querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('Langzeit starten'));
    expect(start?.disabled).toBe(false);
  });

  it('offers privacy-explicit segment and direct-live transcript display modes', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.setTab('long');
    fixture.detectChanges();

    const mode = (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLSelectElement>('[data-testid="voice-long-run-display-mode"]');
    expect(mode?.textContent).toContain('Nach Segmentabschluss');
    expect(mode?.textContent).toContain('Direkt live');
    expect(component.longRunDisplayMode).toBe('segment');
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-segment-privacy-hint"]')?.textContent)
      .toContain('erst als abgeschlossenes');

    component.longRunDisplayMode = 'live';
    fixture.detectChanges();
    expect(component.longRunDisplayMode).toBe('live');
    component.longRunActive = true;
    const observer = (component as any).longRunObserver();
    observer.livePreviewStarted(0);
    observer.livePreview({
      liveRunId: 'voice-long-run-a',
      segmentSequence: 0,
      streamSessionId: 'preview-a',
      text: '<b>ungeprüfter Rohtext</b>',
      event: { event_type: 'partial', payload: { text: '<b>ungeprüfter Rohtext</b>' } },
    });
    fixture.detectChanges();
    expect(component.longRunDisplayMode).toBe('live');
    expect(component.longRunActive).toBe(true);
    expect(component.longRunPreviewSequence).toBe(0);
    expect(component.activeTab).toBe('long');

    const preview = (fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-live-preview"]');
    expect(preview?.textContent).toContain('Live-Vorschau · unkorrigiert');
    expect(preview?.textContent).toContain('<b>ungeprüfter Rohtext</b>');
    expect(preview?.querySelector('b')).toBeNull();
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-live-privacy-hint"]')?.textContent)
      .toContain('fortlaufend ausschließlich über den Hub');
  });

  it('never restores a late raw preview after authoritative segment revisions', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.activeTab = 'long';
    component.longRunDisplayMode = 'live';
    component.longRunActive = true;
    const observer = (component as any).longRunObserver();

    observer.livePreviewStarted(0);
    observer.livePreview({
      liveRunId: 'voice-long-run-a', segmentSequence: 0,
      streamSessionId: 'preview-a', text: 'später Rohtext',
      event: { event_type: 'partial', payload: { text: 'später Rohtext' } },
    });
    observer.timelineUpdated({
      segments: [timelineSegment({
        sequence: 0, revision: 1, timeline_revision: 1,
        text_state: 'provisional', correction_status: 'pending',
        text: 'Autoritatives ASR', display_text: 'Autoritatives ASR',
      })],
      composedTranscript: 'Autoritatives ASR',
      highestTimelineRevision: 1,
      hasPendingRevisions: true,
    });
    observer.livePreview({
      liveRunId: 'voice-long-run-a', segmentSequence: 0,
      streamSessionId: 'preview-a', text: 'veraltete späte Antwort',
      event: { event_type: 'partial', payload: { text: 'veraltete späte Antwort' } },
    });
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-live-preview"]')).toBeNull();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Autoritatives ASR');
    expect((fixture.nativeElement as HTMLElement).textContent).not.toContain('veraltete späte Antwort');

    observer.timelineUpdated({
      segments: [timelineSegment({
        sequence: 0, revision: 2, timeline_revision: 2,
        text_state: 'final', correction_status: 'completed',
        text: 'Korrigierter Text.', display_text: 'Korrigierter Text.',
      })],
      composedTranscript: 'Korrigierter Text.',
      highestTimelineRevision: 2,
      hasPendingRevisions: false,
    });
    observer.livePreviewStarted(1);
    fixture.detectChanges();

    const preview = (fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-live-preview"]');
    expect(preview?.textContent).toContain('Segment 2');
    expect(preview?.textContent).toContain('Live-Erkennung wird verbunden');
    expect((fixture.nativeElement as HTMLElement).querySelectorAll('[data-sequence="0"]')).toHaveLength(1);
  });

  it('shows a recovered live mode before resume while its selector is locked', async () => {
    recoveryValue = recoveredMetadata({ displayMode: 'live' });
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await vi.waitFor(() => expect(fixture.componentInstance.longRunRecovery).toBeTruthy());
    fixture.componentInstance.activeTab = 'long';
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const mode = (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLSelectElement>('[data-testid="voice-long-run-display-mode"]');
    expect(fixture.componentInstance.longRunDisplayMode).toBe('live');
    expect(mode?.value).toBe('live');
    expect(mode?.disabled).toBe(true);
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-live-privacy-hint"]')).toBeTruthy();
  });

  it('starts one continuous supervised capture with bounded rolling-segment settings', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.activeTab = 'long';
    component.selectedCaptureSource = 'system_audio';
    component.longRunSegmentSeconds = 90;
    component.longRunMaxHours = 8;

    await component.startLongRun();

    expect(capture.prepare).toHaveBeenCalledWith('system_audio');
    expect(api.createLongRun).toHaveBeenCalledWith(
      'http://hub.test',
      expect.objectContaining({
        source: 'system_audio',
        segment_duration_seconds: 90,
        max_duration_seconds: 28_800,
        overlap_milliseconds: 1_000,
      }),
      expect.stringContaining('voice-ui:console-long-run:create:'),
    );
    expect(capture.start).toHaveBeenCalledTimes(1);
    expect(capture.start).toHaveBeenCalledWith(
      expect.any(Function), expect.any(Function), expect.any(Function),
      { maxDurationSeconds: 28_800 },
    );
    expect(component.longRunActive).toBe(true);
    fixture.detectChanges();
    expect(component.longRunRecovery?.runId).toBe('voice-long-run-a');
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-recovery"]')).toBeNull();

    await component.stopLongRun();
    expect(capture.stop).toHaveBeenCalledTimes(1);
    expect(api.stopLongRun).toHaveBeenCalledWith(
      'http://hub.test', 'voice-long-run-a',
      expect.objectContaining({ reason: 'user_stop' }),
      'voice-ui:long-run-stop:voice-long-run-a',
    );
    expect(component.longRunTranscript).toBe('Fertiges Transkript');
  });

  it('renders a visible warning for irrecoverable encrypted-buffer gaps', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.activeTab = 'long';
    (component as any).longRunObserver().gapsUpdated([3, 4]);
    component.longRunWarning = 'Puffer ausgelastet.';
    fixture.detectChanges();

    const warning = (fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-gap-warning"]');
    expect(warning?.textContent).toContain('Puffer ausgelastet');
    expect(warning?.textContent).toContain('Segmente: 4, 5');
    const gaps = (fixture.nativeElement as HTMLElement)
      .querySelectorAll('[data-text-state="gap"]');
    expect([...gaps].map((item) => item.getAttribute('data-sequence'))).toEqual(['3', '4']);
  });

  it('renders a persisted failed ASR segment as one terminal gap row', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.setTab('long');
    (component as any).longRunObserver().timelineUpdated({
      segments: [timelineSegment({
        sequence: 2,
        status: 'failed',
        revision: 0,
        timeline_revision: 7,
        text_state: 'none',
        correction_status: 'not_requested',
        text: '',
        display_text: '',
      })],
      composedTranscript: '',
      highestTimelineRevision: 7,
      hasPendingRevisions: false,
    });
    (component as any).longRunObserver().gapsUpdated([2]);
    fixture.detectChanges();

    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('[data-sequence="2"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute('data-text-state')).toBe('gap');
    expect(rows[0].textContent).toContain('Lücke');
    expect(rows[0].textContent).toContain('Nicht wiederherstellbare Segmentlücke');
    expect(rows[0].textContent).not.toContain('wird transkribiert');
  });

  it('removes a healed Hub gap and counts an out-of-order confirmation exactly once', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.setTab('long');
    const observer = (component as any).longRunObserver();

    observer.gap(0);
    observer.gapsUpdated([0]);
    observer.gapsUpdated([]);
    (component as any).applyLongRunResponse({
      run: { id: 'voice-long-run-a', status: 'active', version: 3 },
      segment: { sequence: 1, status: 'completed' },
      segments: [],
      gaps: [],
      resume: { next_sequence: 0, acknowledged_through_sequence: -1 },
    });
    fixture.detectChanges();

    expect(component.longRunGapSequences).toEqual([]);
    expect(component.longRunUploadedSegments).toBe(1);
    expect(component.longRunWarning).toBe('');
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-gap-warning"]')).toBeNull();
  });

  it('keeps recovery metadata current but only renders it after capture is interrupted', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.setTab('long');
    component.longRunActive = true;
    (component as any).longRunObserver().recoveryUpdated(recoveredMetadata({
      nextSequence: 1,
      timelineMilliseconds: 149_000,
    }));
    fixture.detectChanges();

    expect(component.longRunRecovery).toEqual(expect.objectContaining({
      nextSequence: 1,
      timelineMilliseconds: 149_000,
    }));
    expect((fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-recovery"]')).toBeNull();

    component.longRunActive = false;
    (component as any).longRunObserver().recoveryUpdated(component.longRunRecovery!);
    fixture.detectChanges();
    const recoveryCard = (fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-recovery"]');
    expect(recoveryCard?.textContent).toContain('Cursor 1');
    expect(recoveryCard?.textContent).toContain('00:02:29');
  });

  it('replaces one keyed provisional section with its corrected revision', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.setTab('long');
    const observer = (component as any).longRunObserver();
    observer.timelineUpdated({
      segments: [timelineSegment({
      sequence: 2,
      revision: 1,
      timeline_revision: 5,
      text_state: 'provisional',
      correction_status: 'pending',
      text: 'roher Texd',
      display_text: 'roher Texd',
      })],
      composedTranscript: 'roher Texd',
      highestTimelineRevision: 5,
      hasPendingRevisions: true,
    });
    fixture.detectChanges();

    let section = (fixture.nativeElement as HTMLElement).querySelector('[data-sequence="2"]');
    expect(section?.textContent).toContain('vorläufig');
    expect(section?.textContent).toContain('roher Texd');

    observer.timelineUpdated({
      segments: [timelineSegment({
      sequence: 2,
      revision: 2,
      timeline_revision: 6,
      text_state: 'final',
      correction_status: 'completed',
      text: 'Korrigierter Text.',
      display_text: 'Korrigierter Text.',
      })],
      composedTranscript: 'Korrigierter Text.',
      highestTimelineRevision: 6,
      hasPendingRevisions: false,
    });
    fixture.detectChanges();

    section = (fixture.nativeElement as HTMLElement).querySelector('[data-sequence="2"]');
    expect(section?.textContent).toContain('korrigiert');
    expect(section?.textContent).toContain('Korrigierter Text.');
    expect(section?.textContent).not.toContain('roher Texd');
    expect((fixture.nativeElement as HTMLElement).querySelectorAll('[data-sequence="2"]')).toHaveLength(1);
  });

  it('renders transcript revisions as text instead of executable markup', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.componentInstance.setTab('long');
    (fixture.componentInstance as any).longRunObserver().timelineUpdated({
      segments: [timelineSegment({
        text: '<img src=x onerror=alert(1)>',
        display_text: '<img src=x onerror=alert(1)>',
      })],
      composedTranscript: '<img src=x onerror=alert(1)>',
      highestTimelineRevision: 1,
      hasPendingRevisions: false,
    });
    fixture.detectChanges();

    const transcript = (fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-long-run-transcript"]');
    expect(transcript?.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(transcript?.querySelector('img')).toBeNull();
  });

  it('checks a recovered Hub run before requesting a fresh audio permission', async () => {
    recoveryValue = {
      schemaVersion: 1,
      runId: 'voice-long-run-a',
      hubUrl: 'http://hub.test',
      createIdempotencyKey: 'stable-create-key',
      profileGeneration: Date.now(),
      request: {
        source: 'system_audio',
        profile_id: 'default',
        segment_duration_seconds: 120,
        max_duration_seconds: 28_800,
        overlap_milliseconds: 1_000,
      },
      nextSequence: 0,
      timelineMilliseconds: 0,
      updatedAt: Date.now(),
    };
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await vi.waitFor(() => expect(fixture.componentInstance.longRunRecoveryReady()).toBe(true));
    const component = fixture.componentInstance;
    component.activeTab = 'long';

    await component.startLongRun(true);

    expect(api.getLongRun).toHaveBeenCalledWith(
      'http://hub.test', 'voice-long-run-a', { includeText: false },
    );
    expect(api.getLongRun.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(capture.prepare).mock.invocationCallOrder[0],
    );
    expect(capture.prepare).toHaveBeenCalledWith('system_audio');
    expect(api.createLongRun).not.toHaveBeenCalled();
    await component.stopLongRun();
  });

  it('keeps secure recovery available when the Hub status check is temporarily offline', async () => {
    recoveryValue = recoveredMetadata();
    api.getLongRun.mockReturnValue(throwError(() => ({ status: 0, message: 'offline' })));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await vi.waitFor(() => expect(fixture.componentInstance.longRunWarning).toContain('Hub-Status'));
    clickLongRunTab(fixture.nativeElement as HTMLElement);
    fixture.detectChanges();

    expect(fixture.componentInstance.longRunStorageAvailable).toBe(true);
    expect(fixture.componentInstance.longRunRecovery?.runId).toBe('voice-long-run-a');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Hub-Status prüfen');
    expect(capture.prepare).not.toHaveBeenCalled();
  });

  it('offers drain-only recovery after the capture deadline and never reopens audio capture', async () => {
    const generation = Date.now();
    recoveryValue = recoveredMetadata({
      profileGeneration: generation,
      nextSequence: 1,
      timelineMilliseconds: 60_000,
      completedTimelineMilliseconds: 60_000,
      durableNextSequence: 1,
      durableTimelineMilliseconds: 60_000,
    });
    api.getLongRun.mockReturnValue(of({
      run: {
        id: 'voice-long-run-a', status: 'active',
        capture_deadline_at: Date.now() - 1_000,
        expires_at: Date.now() + 60_000,
      },
      segments: [], gaps: [], composed_transcript: null,
      resume: { next_sequence: 0, acknowledged_through_sequence: -1 },
    }));
    const metadata = {
      runId: 'voice-long-run-a', profileId: 'default', profileGeneration: generation,
      sequence: 0, startedAtMs: 0, endedAtMs: 60_000, durationMs: 60_000,
      overlapMilliseconds: 0, idempotencyKey: 'voice-ui:long-run-segment:voice-long-run-a:0',
      byteLength: 120, createdAt: 1, expiresAt: Number.MAX_SAFE_INTEGER,
    };
    vi.mocked(spool.list)
      .mockResolvedValueOnce([metadata])
      .mockResolvedValueOnce([metadata])
      .mockResolvedValue([]);
    vi.mocked(spool.read).mockResolvedValueOnce({
      ...metadata,
      audio: new ArrayBuffer(120),
    });
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await vi.waitFor(() => expect(fixture.componentInstance.longRunRecoveryDrainOnly()).toBe(true));
    clickLongRunTab(fixture.nativeElement as HTMLElement);
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Puffer senden und abschließen');
    await fixture.componentInstance.drainLongRunRecovery();

    expect(api.uploadLongRunSegment).toHaveBeenCalledTimes(1);
    expect(capture.prepare).not.toHaveBeenCalled();
    expect(capture.start).not.toHaveBeenCalled();
    expect(fixture.componentInstance.longRunRecovery).toBeNull();
    expect(fixture.componentInstance.successMessage).toContain('Puffer wurde gesendet');
  });

  it('reports an unconfirmed Hub state separately after an offline local discard', async () => {
    recoveryValue = recoveredMetadata();
    api.getLongRun.mockReturnValue(throwError(() => ({ status: 0, message: 'offline' })));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    await vi.waitFor(() => expect(fixture.componentInstance.longRunRecovery).toBeTruthy());

    await fixture.componentInstance.discardLongRunRecovery();

    expect(fixture.componentInstance.longRunRecovery).toBeNull();
    expect(fixture.componentInstance.successMessage).toContain('Puffer wurde gelöscht');
    expect(fixture.componentInstance.longRunWarning).toContain('Run-Status konnte nicht bestätigt');
  });

  it('requests system-audio consent before saving configuration or creating a live Hub stream', async () => {
    api.createStream.mockReturnValue(of({
      stream: { session_id: 'voice-stream-a', state: 'created', next_chunk_sequence: 0 },
    }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.selectedCaptureSource = 'system_audio';

    await component.startLive();

    expect(capture.prepare).toHaveBeenCalledWith('system_audio');
    expect(vi.mocked(capture.prepare).mock.invocationCallOrder[0]).toBeLessThan(
      api.saveConfiguration.mock.invocationCallOrder[0],
    );
    expect(api.saveConfiguration.mock.invocationCallOrder[0]).toBeLessThan(
      api.createStream.mock.invocationCallOrder[0],
    );
    expect(api.createStream).toHaveBeenCalledWith(
      'http://hub.test',
      expect.objectContaining({ deadline_seconds: 300, max_audio_seconds: 120 }),
      expect.any(String),
    );
  });

  it('records system audio locally and keeps the source out of Hub profile configuration', async () => {
    vi.mocked(batch.stop).mockResolvedValueOnce(new Blob(['audio'], { type: 'audio/webm' }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.activeTab = 'batch';
    component.selectedCaptureSource = 'system_audio';

    await component.startBatchRecording();
    await component.stopBatchRecording();

    expect(batch.start).toHaveBeenCalledWith('system_audio', expect.objectContaining({
      ended: expect.any(Function),
      error: expect.any(Function),
    }));
    expect(component.batchFileName).toBe('voice-recording.webm');
    expect(api.saveConfiguration).not.toHaveBeenCalled();
    expect(component.batchAudio).toBeInstanceOf(Blob);
  });

  it('turns an automatically ended browser share into a ready local batch recording', async () => {
    let ended: ((reason?: string) => void) | undefined;
    vi.mocked(batch.start).mockImplementationOnce(async (_source, observer) => {
      ended = observer?.ended;
    });
    vi.mocked(batch.stop).mockResolvedValueOnce(new Blob(['audio'], { type: 'audio/webm' }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.activeTab = 'batch';
    component.selectedCaptureSource = 'system_audio';
    await component.startBatchRecording();

    ended?.('source_ended');

    await vi.waitFor(() => expect(component.batchAudio).toBeInstanceOf(Blob));
    expect(component.batchRecording).toBe(false);
    expect(component.batchBusy).toBe(false);
    expect(component.successMessage).toContain('Audiofreigabe wurde beendet');
  });

  it('explains the native safety limit without claiming that the share was revoked', async () => {
    let ended: ((reason?: string) => void) | undefined;
    vi.mocked(batch.start).mockImplementationOnce(async (_source, observer) => {
      ended = observer?.ended;
    });
    vi.mocked(batch.stop).mockResolvedValueOnce(new Blob(['audio'], { type: 'audio/wav' }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.activeTab = 'batch';
    await component.startBatchRecording();

    ended?.('safety_limit');

    await vi.waitFor(() => expect(component.batchAudio).toBeInstanceOf(Blob));
    expect(component.successMessage).toContain('maximale Aufnahmedauer');
    expect(component.successMessage).not.toContain('Audiofreigabe wurde beendet');
  });

  it('does not continue from a prepared share to Hub startup after component destruction', async () => {
    const saveResult = new Subject<any>();
    api.saveConfiguration.mockReturnValueOnce(saveResult);
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.selectedCaptureSource = 'system_audio';
    const starting = component.startLive();
    await vi.waitFor(() => expect(api.saveConfiguration).toHaveBeenCalled());

    component.ngOnDestroy();
    saveResult.next({
      schema_version: 'ananta.voice-configuration.v1',
      scope: 'profile', scope_id: 'default', delta: {}, version: 2,
    });
    saveResult.complete();
    await starting;

    expect(api.createStream).not.toHaveBeenCalled();
    expect(capture.stop).toHaveBeenCalled();
  });

  it('locks configuration and tab selection throughout capture startup', async () => {
    let resolvePrepare!: () => void;
    vi.mocked(capture.prepare).mockImplementationOnce(() => new Promise<void>((resolve) => {
      resolvePrepare = resolve;
    }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const start = [...fixture.nativeElement.querySelectorAll('button')]
      .find((button: HTMLButtonElement) => button.textContent?.trim() === 'Live starten') as HTMLButtonElement;

    expect(start.disabled).toBe(false);
    start.click();
    expect(fixture.componentInstance.liveBusy).toBe(true);
    expect(fixture.componentInstance.configurationInteractionLocked()).toBe(true);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const profile = fixture.nativeElement.querySelector('input[placeholder="default"]') as HTMLInputElement;
    const tabs = [...fixture.nativeElement.querySelectorAll('[role="tab"]')] as HTMLButtonElement[];
    expect(profile.disabled).toBe(true);
    expect(tabs.every((tab) => tab.disabled)).toBe(true);

    fixture.componentInstance.ngOnDestroy();
    resolvePrepare();
    await Promise.resolve();
  });

  it('persists the additive Vosk plus generative rewrite profile contract', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.selectedCorrectorModel = 'phi-4-mini';

    await component.saveConfiguration();

    expect(api.saveConfiguration).toHaveBeenCalledWith('http://hub.test', expect.objectContaining({
      scope: 'profile',
      scope_id: 'default',
      delta: expect.objectContaining({
        transport_mode: 'streaming',
        primary_backend: 'vosk',
        correction_policy: 'generative_rewrite',
        review_policy: 'always',
        generative_corrector_provider: 'embedded',
        generative_corrector_model: 'phi-4-mini',
        secondary_backends: [],
        feature_flags: { generative_corrector: true, voice_fusion: false },
      }),
    }), expect.stringContaining('voice-ui:console-configuration:profile:'));
  });

  it('merges console fields into the versioned scope delta without deleting foreign settings', async () => {
    api.getConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        recognition_strategy: 'single', primary_backend: 'vosk', correction_policy: 'deterministic',
        generative_corrector_model: 'gemma-3-4b-it',
      },
      sources: [{
        scope: 'profile', scope_id: 'default', version: 7,
        delta: {
          routing_strategy: 'adaptive',
          confidence_threshold: .82,
          feature_flags: { personalization: true, adaptive_routing: true },
        },
      }],
      version: 'effective-7',
    }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.generativeCorrection = true;
    component.selectedCorrectorModel = 'gemma-3-4b-it';

    await component.saveConfiguration();

    expect(api.saveConfiguration).toHaveBeenCalledWith('http://hub.test', expect.objectContaining({
      expected_version: 7,
      delta: expect.objectContaining({
        routing_strategy: 'adaptive',
        confidence_threshold: .82,
        feature_flags: expect.objectContaining({
          personalization: true,
          adaptive_routing: true,
          generative_corrector: true,
          voice_fusion: false,
        }),
      }),
    }), expect.any(String));
    expect(api.getConfiguration).toHaveBeenCalledTimes(2);
  });

  it('disables unavailable ASR backends and stale corrector selections from Hub capabilities', () => {
    api.getConfigurationSchema.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      fields: [
        { key: 'recognition_strategy', type: 'enum', enum: ['single'] },
        { key: 'primary_backend', type: 'enum', enum: ['vosk', 'whisper_cpp'] },
        { key: 'generative_corrector_model', type: 'string' },
      ],
    }));
    api.getCapabilities.mockReturnValue(of({
      available: true,
      provider: 'voice-runtime',
      capabilities: ['streaming'],
      models: [
        { id: 'vosk-de', backend: 'vosk', available: false, status: 'unavailable', reason_code: 'model_missing' },
        { id: 'whisper-small', engine: 'whisper_cpp', status: 'ready' },
        { id: 'whisper-fast', engine: 'faster_whisper', status: 'ready' },
        { id: 'voxtral-mini', engine: 'voxtral', status: 'unavailable', reason_code: 'model_missing' },
      ],
      model_catalog: [
        { id: 'vosk-de', engine: 'vosk', status: 'unavailable', reason_code: 'manifest_missing' },
        { id: 'whisper-small', engine: 'whisper_cpp', status: 'ready' },
      ],
      correction_models: [{ id: 'phi-4-mini', available: true, role: 'generative_corrector' }],
    }));
    api.getConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        recognition_strategy: 'single', primary_backend: 'vosk', correction_policy: 'generative_rewrite',
        generative_corrector_model: 'retired-gemma', feature_flags: { generative_corrector: true },
      },
      sources: [], version: 1,
    }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    expect(component.asrBackends()).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'vosk', available: false, reason: 'model_missing' }),
      expect.objectContaining({ id: 'whisper_cpp', label: 'whisper.cpp', available: true }),
      expect.objectContaining({ id: 'faster_whisper', label: 'faster-whisper', available: true }),
      expect.objectContaining({ id: 'voxtral', label: 'Voxtral', available: false }),
    ]));
    expect(component.backendReason('vosk')).toBe('model_missing');
    expect(component.validBackendSelection()).toBe(false);
    component.selectedBackend = 'whisper_cpp';
    expect(component.validBackendSelection()).toBe(true);
    component.selectedRecognitionStrategy = 'parallel_compare';
    component.selectedSecondaryBackend = 'vosk';
    expect(component.validBackendSelection()).toBe(false);
    expect(component.correctorModels()).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'phi-4-mini', available: true }),
      expect.objectContaining({ id: 'retired-gemma', available: false, reason: 'voice.corrector.not_reported' }),
    ]));
    expect(component.validCorrectionSelection()).toBe(false);
  });

  it('filters provider-aware models and persists inherit without endpoint material', async () => {
    api.getCapabilities.mockReturnValue(of({
      available: true,
      provider: 'voice-runtime',
      capabilities: ['generative_rewrite'],
      models: [{ id: 'vosk-de', backend: 'vosk', available: true }],
      correction_providers: [
        { id: 'embedded', display_name: 'Embedded', available: true, supports_manual_model: false },
        { id: 'ollama', display_name: 'Ollama', available: true, supports_manual_model: true },
        { id: 'lmstudio', display_name: 'LM Studio', available: true, supports_manual_model: true },
      ],
      correction_default: {
        provider: 'ollama', model: 'llama3.2:latest', source: 'default_provider/default_model', available: true,
      },
      correction_models: [
        { id: 'qwen-local', provider: 'embedded', available: true, role: 'generative_corrector' },
        { id: 'shared-model', provider: 'ollama', available: true, role: 'generative_corrector' },
        { id: 'llama3.2:latest', provider: 'ollama', available: true, role: 'generative_corrector' },
        { id: 'shared-model', provider: 'lmstudio', available: true, role: 'generative_corrector' },
      ],
    }));
    api.getConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        recognition_strategy: 'single', primary_backend: 'vosk', correction_policy: 'generative_rewrite',
        generative_corrector_provider: 'ollama', generative_corrector_model: 'shared-model',
        feature_flags: { generative_corrector: true },
      },
      sources: [], version: 1,
    }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    expect(component.selectedCorrectorProvider).toBe('ollama');
    expect(component.correctorModels('ollama').map((choice) => choice.id)).toEqual([
      'shared-model', 'llama3.2:latest',
    ]);
    expect(component.correctorModels('lmstudio').map((choice) => choice.id)).toEqual(['shared-model']);
    expect(component.correctorProviders()).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'inherit', label: 'Allgemeine LLM-Vorgabe', available: true }),
      expect.objectContaining({ id: 'ollama', available: true }),
      expect.objectContaining({ id: 'lmstudio', available: true }),
    ]));
    const deploymentNotice = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(deploymentNotice).toContain('übernimmt nur Provider und Modell');
    expect(deploymentNotice).toContain('LMSTUDIO_URL');
    expect(deploymentNotice).toContain('LMSTUDIO_API_KEY');
    expect(deploymentNotice).toContain('OLLAMA_URL');
    expect(deploymentNotice).toContain('OLLAMA_API_KEY');
    expect(deploymentNotice).toContain('Neuerstellung des Corrector-Workers');
    expect(deploymentNotice).toContain('ein bloßer Neustart lädt sie nicht neu');
    expect(deploymentNotice).toContain('docs/voice-quickstart.md');
    expect((fixture.nativeElement as HTMLElement).querySelector('a[href="/settings?section=llm"]')).toBeTruthy();

    component.onCorrectorProviderChange('inherit');
    expect(component.selectedCorrectorModel).toBe('');
    expect(component.validCorrectionSelection()).toBe(true);
    await component.saveConfiguration();

    const mutation = api.saveConfiguration.mock.calls.at(-1)?.[1];
    expect(mutation.delta).toEqual(expect.objectContaining({
      generative_corrector_provider: 'inherit',
      generative_corrector_model: '',
    }));
    expect(mutation.delta).not.toHaveProperty('base_url');
    expect(mutation.delta).not.toHaveProperty('api_key');
  });

  it('accepts a manual model ID for a configured provider even when discovery is offline', async () => {
    api.getCapabilities.mockReturnValue(of({
      available: true,
      provider: 'voice-runtime',
      capabilities: ['generative_rewrite'],
      models: [{ id: 'vosk-de', backend: 'vosk', available: true }],
      correction_providers: [
        { id: 'embedded', display_name: 'Embedded', available: true, supports_manual_model: false },
        { id: 'vllm_local', display_name: 'vLLM Local', available: false, supports_manual_model: true },
      ],
      correction_models: [{
        id: 'qwen-local', provider: 'embedded', available: true, role: 'generative_corrector',
      }],
    }));
    api.getConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        recognition_strategy: 'single', primary_backend: 'vosk', correction_policy: 'generative_rewrite',
        generative_corrector_provider: 'vllm_local', generative_corrector_model: 'Qwen/Qwen2.5-7B-Instruct',
        feature_flags: { generative_corrector: true },
      },
      sources: [], version: 1,
    }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    expect(component.manualCorrectorModel).toBe(true);
    expect(component.manualCorrectorModelId).toBe('Qwen/Qwen2.5-7B-Instruct');
    expect(component.validCorrectionSelection()).toBe(true);
    await component.saveConfiguration();
    expect(api.saveConfiguration).toHaveBeenCalledWith('http://hub.test', expect.objectContaining({
      delta: expect.objectContaining({
        generative_corrector_provider: 'vllm_local',
        generative_corrector_model: 'Qwen/Qwen2.5-7B-Instruct',
      }),
    }), expect.any(String));

    vi.clearAllMocks();
    component.manualCorrectorModelId = 'not valid model';
    expect(component.validCorrectionSelection()).toBe(false);
    component.manualCorrectorModelId = 'm'.repeat(182);
    expect(component.validCorrectionSelection()).toBe(false);
    await component.saveConfiguration();
    expect(api.saveConfiguration).not.toHaveBeenCalled();

    component.onCorrectorProviderChange('embedded');
    component.setManualCorrectorModel(true);
    expect(component.correctorProviderSupportsManual()).toBe(false);
    expect(component.validCorrectionSelection()).toBe(false);
  });
});

function recoveredMetadata(
  overrides: Partial<VoiceLongRunRecoveryMetadata> = {},
): VoiceLongRunRecoveryMetadata {
  return {
    schemaVersion: 1,
    runId: 'voice-long-run-a',
    hubUrl: 'http://hub.test',
    createIdempotencyKey: 'stable-create-key',
    profileGeneration: Date.now(),
    request: {
      source: 'system_audio',
      profile_id: 'default',
      segment_duration_seconds: 120,
      max_duration_seconds: 28_800,
      overlap_milliseconds: 1_000,
    },
    nextSequence: 0,
    timelineMilliseconds: 0,
    updatedAt: Date.now(),
    ...overrides,
  };
}

function timelineSegment(
  overrides: Partial<VoiceLongRunTimelineSegment> = {},
): VoiceLongRunTimelineSegment {
  return {
    sequence: 0,
    status: 'completed',
    revision: 2,
    timeline_revision: 1,
    text_state: 'final',
    correction_status: 'completed',
    text: 'Korrigierter Text.',
    display_text: 'Korrigierter Text.',
    ...overrides,
  };
}

function clickLongRunTab(element: HTMLElement): void {
  const tab = [...element.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
    .find((candidate) => candidate.textContent?.includes('Langzeit'));
  if (!tab) throw new Error('long-run tab missing');
  tab.click();
}
