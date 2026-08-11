import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { BehaviorSubject, of, Subject } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { LivekitSfuTransportService } from '../../services/livekit-sfu-transport.service';
import { SpeechReconciliationApiService } from '../../services/speech-reconciliation-api.service';
import { WebrtcMediaSessionService } from '../../services/webrtc-media-session.service';
import { SemanticMediaProgramHostComponent } from './semantic-media-program-host.component';
import { SemanticMediaProgramFacade, SemanticMediaProgramHostView } from './semantic-media-program.facade';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => {
    const file = path.basename(String(resource));
    const directory = file.startsWith('pair-compute-') ? 'pair-view' : 'voice';
    return readFile(path.resolve(process.cwd(), 'src/app/features', directory, file), 'utf8');
  });
});

describe('SemanticMediaProgramHostComponent ordinary media wiring', () => {
  it('routes reachable Voice-host microphone, camera, screen, replace and stop gestures to the facade', async () => {
    const view$ = new BehaviorSubject<SemanticMediaProgramHostView>(hostView());
    const facade = {
      view$,
      start: vi.fn(async () => undefined),
      handleProgramIntent: vi.fn(), handleComputeIntent: vi.fn(), requestComputeSuggestion: vi.fn(),
      handleReceiverPathIntent: vi.fn(), handleEvidenceConsentIntent: vi.fn(), startSpeech: vi.fn(),
      stopSpeech: vi.fn(), handleSpeechSettings: vi.fn(), handleEvidencePropose: vi.fn(),
      handleEvidenceAccept: vi.fn(), pauseEvidence: vi.fn(), resumeEvidence: vi.fn(), rejectEvidence: vi.fn(),
      revokeEvidence: vi.fn(), requestEvidenceCuration: vi.fn(), handleEvidenceLocalOverride: vi.fn(),
      startOrdinaryMicrophone: vi.fn(), stopOrdinaryMicrophone: vi.fn(), setOrdinaryMicrophoneMuted: vi.fn(),
      startOrdinaryVideo: vi.fn(), stopOrdinaryVideo: vi.fn(), replaceOrdinaryVideo: vi.fn(),
      setOrdinaryVideoMuted: vi.fn(),
      grantOrdinaryMediaPublicationConsent: vi.fn(),
      revokeOrdinaryMediaPublicationConsent: vi.fn(),
    };
    await TestBed.configureTestingModule({
      imports: [SemanticMediaProgramHostComponent],
      providers: [
        { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub.test' }] } },
        { provide: SpeechReconciliationApiService, useValue: { list: () => of({ jobs: [], next_offset: null }) } },
        {
          provide: WebrtcMediaSessionService,
          useValue: { remoteTracks$: new BehaviorSubject<readonly unknown[]>([]) },
        },
        { provide: LivekitSfuTransportService, useValue: { remoteTrack$: new Subject() } },
      ],
    }).overrideComponent(SemanticMediaProgramHostComponent, {
      set: { providers: [{ provide: SemanticMediaProgramFacade, useValue: facade }] },
    }).compileComponents();

    const fixture = TestBed.createComponent(SemanticMediaProgramHostComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(facade.start).toHaveBeenCalled();

    const click = (label: string) => {
      const button = [...fixture.nativeElement.querySelectorAll('button')]
        .find((value: HTMLButtonElement) => value.textContent?.includes(label)) as HTMLButtonElement | undefined;
      expect(button, `button ${label}`).toBeDefined();
      button!.click();
      fixture.detectChanges();
    };
    click('Mikrofon freigeben');
    click('Kamera freigeben');
    click('Bildschirm freigeben');
    view$.next({
      ...hostView(),
      ordinaryMediaPublications: [{
        publicationId: 'ordinary-camera-3', source: 'camera', status: 'active', local: true,
        trackId: 'camera-track', captureLabel: 'Kamera', reasonCode: null,
      }],
    });
    fixture.detectChanges();
    click('Kamera wechseln');
    click('Kamera stoppen');

    expect(facade.startOrdinaryMicrophone).toHaveBeenCalledTimes(1);
    expect(facade.startOrdinaryVideo).toHaveBeenCalledWith('camera');
    expect(facade.startOrdinaryVideo).toHaveBeenCalledWith('screen');
    expect(facade.replaceOrdinaryVideo).toHaveBeenCalledWith('ordinary-camera-3');
    expect(facade.stopOrdinaryVideo).toHaveBeenCalledWith('ordinary-camera-3');

    facade.startOrdinaryMicrophone.mockClear();
    facade.startOrdinaryVideo.mockClear();
    view$.next({
      ...hostView(),
      ordinaryMediaAuthority: 'public',
      ordinaryMediaCaptureEnabled: false,
      ordinaryMediaVideoCaptureEnabled: false,
      ordinaryMediaPublicationPreparationPending: true,
      ordinaryMediaPublicationConsent: publicationConsent('unbound'),
    });
    fixture.componentRef.setInput('displayMode', 'pair_media');
    fixture.detectChanges();
    let consentButton = [...fixture.nativeElement.querySelectorAll('button')]
      .find((value: HTMLButtonElement) => value.textContent?.includes('Einwilligen')) as HTMLButtonElement;
    expect(consentButton.disabled).toBe(true);
    expect(fixture.nativeElement.textContent).toContain('wird vorbereitet');

    view$.next({
      ...hostView(),
      ordinaryMediaAuthority: 'public',
      ordinaryMediaCaptureEnabled: false,
      ordinaryMediaVideoCaptureEnabled: false,
      ordinaryMediaPublicationPreparationPending: false,
      ordinaryMediaPublicationConsent: publicationConsent('unbound'),
    });
    fixture.detectChanges();
    consentButton = [...fixture.nativeElement.querySelectorAll('button')]
      .find((value: HTMLButtonElement) => value.textContent?.includes('Einwilligen')) as HTMLButtonElement;
    expect(consentButton.disabled).toBe(false);
    click('Einwilligen und Medien aktivieren');
    expect(facade.grantOrdinaryMediaPublicationConsent).toHaveBeenCalledWith({ kind: 'session' });
    expect(facade.startOrdinaryMicrophone).not.toHaveBeenCalled();
    expect(facade.startOrdinaryVideo).not.toHaveBeenCalled();

    view$.next({
      ...hostView(),
      ordinaryMediaAuthority: 'public',
      ordinaryMediaPublicationConsent: publicationConsent('failed'),
    });
    fixture.detectChanges();
    click('Sicher zurücksetzen');
    expect(facade.revokeOrdinaryMediaPublicationConsent).toHaveBeenCalledTimes(1);

    view$.next({
      ...hostView(),
      ordinaryMediaAuthority: 'public',
      ordinaryMediaE2eeReady: true,
      ordinaryMediaPublicationConsent: publicationConsent('granted'),
    });
    fixture.detectChanges();
    click('Eigene Freigabe deaktivieren');
    expect(facade.revokeOrdinaryMediaPublicationConsent).toHaveBeenCalledTimes(2);
  });
});

function hostView(): SemanticMediaProgramHostView {
  return {
    scope: {
      direction: 'bidirectional', dataClass: 'ordinary_media', purpose: 'pair_session',
      retentionLabel: 'keine', trainerLocation: 'keine', e2eeMode: 'strict_e2ee', ordinaryFallback: 'aktiv',
    },
    capabilities: [{
      capability: 'ordinary_media', label: 'Ordinary Audio/Video', sensitive: false,
      state: 'authoritatively_active', requestId: null,
    }],
    online: true,
    hubUrl: 'http://hub.test',
    ordinaryMediaAuthority: 'hub',
    ordinaryMediaActivationEnabled: true,
    ordinaryMediaPublicationPreparationPending: false,
    computeVisible: false,
    compute: {
      contract: { contractId: '', revision: 0, status: 'absent', profile: 'off', delayMs: 5000, roles: {} },
      leases: [], pending: false, errorCode: null,
    },
    receiverPaths: [],
    ordinaryMediaCaptureEnabled: true,
    ordinaryMediaVideoCaptureEnabled: true,
    ordinaryMediaE2eeReady: false,
    ordinaryMediaReason: 'ordinary_media_ready',
    ordinaryMediaPublicationConsent: publicationConsent('unbound'),
    ordinaryAudioState: { status: 'idle', trackId: null, deviceLabelVisible: false, reasonCode: null },
    ordinaryMediaPublications: [],
    sfuRemoteVideos: [],
    speechTransportState: 'stopped', speechTransportReason: 'not_started', speechTransportCanStart: false,
    speechSettings: {
      displayMode: 'segment', segmentDurationSeconds: 30, correctEachSegment: true,
      paused: false, ordinaryAudioOverride: false,
    },
    speechQuality: {
      mode: 'ordinary_audio', reasonCode: 'quality_initial', transitioned: false,
      ordinaryAudioAvailable: true, liveTranscriptEnabled: false,
      delayedSourceEnabled: false, semanticFeaturesEnabled: false,
    },
    speechReconciliationHubAuthorized: false,
    evidenceOffer: null, evidenceSync: null, evidenceAvailableReason: 'none',
    evidenceConsent: { bound: false, signerIds: [], consent: null, pending: false, errorCode: 'missing' },
  };
}

function publicationConsent(status: 'unbound' | 'inactive' | 'granted' | 'failed') {
  const binding = status === 'unbound' ? null : {
    sessionId: 'session-a', securityEpoch: 3, contractDigest: 'a'.repeat(64), adapterGeneration: 7,
    localPeerId: 'alice', remotePeerId: 'bob', maxExpiresAtMs: Date.now() + 3_600_000,
  };
  return {
    status,
    binding,
    revision: 1,
    term: status === 'granted' ? { kind: 'session' as const } : null,
    slots: [],
    expiresAtMs: status === 'granted' ? Date.now() + 3_600_000 : null,
    ...(status === 'failed' ? { reasonCode: 'public_media_publication_consent_gate_failed' } : {}),
  } as const;
}
