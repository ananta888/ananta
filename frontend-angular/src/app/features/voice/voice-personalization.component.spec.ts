import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { of } from 'rxjs';

import { VoiceApiService } from './voice-api.service';
import { VoiceLearningContext } from './voice-candidate-review.component';
import { VoicePersonalizationComponent } from './voice-personalization.component';

beforeAll(async () => {
  await ɵresolveComponentResources((resource) => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('VoicePersonalizationComponent', () => {
  const inactiveConsent = {
    id: null,
    profile_id: 'profile-a',
    granted: false,
    categories: [],
    retention_days: null,
    version: 0,
  };
  const activeConsent = {
    id: 'consent-a',
    profile_id: 'profile-a',
    granted: true,
    categories: ['vocabulary', 'text_corrections'] as const,
    retention_days: 90,
    version: 1,
  };
  const snapshot = {
    schema_version: 'voice-personalization-snapshot.v1',
    profile_id: 'profile-a', version: 2, consent_id: 'consent-a', consent_version: 1,
    expires_at: 1, vocabulary: ['Ananta'], substitutions: [], preferences: [], negative_examples: [],
    persistence_owner: 'hub' as const, runtime_persistence_allowed: false as const,
  };
  const context: VoiceLearningContext = {
    review: {
      id: 'review-a', profile_id: 'profile-a', result_ref: 'audit-a', candidate_ids: ['candidate-a'],
      state: 'corrected', selected_candidate_id: 'candidate-a', correction_text: 'Ananta', version: 2,
    },
    sourceText: 'An ander',
    targetText: 'Ananta',
  };
  const api = {
    getConsent: vi.fn(() => of(inactiveConsent)),
    setConsent: vi.fn(() => of(activeConsent)),
    getPersonalizationSnapshot: vi.fn(() => of(snapshot)),
    addPersonalizationFeedback: vi.fn(() => of({ id: 'feedback-a' })),
    exportPersonalization: vi.fn(() => of({
      schema_version: 'voice-personalization.v1', profile_id: 'profile-a', version: 2, items: [],
    })),
    importPersonalization: vi.fn(() => of({ profile_id: 'profile-a', imported_count: 1, version: 3 })),
    resetPersonalization: vi.fn(() => of({ profile_id: 'profile-a', deleted_count: 1, version: 3 })),
    deleteVoiceProfile: vi.fn(() => of({
      profile_id: 'profile-a', deleted_count: 5, deleted_by_store: { feedback: 1 }, snapshots_revoked: true,
      revoked_stream_count: 2, runtime_cleanup_failed_count: 0, runtime_cleanup_pending: false,
    })),
    createFineTuningExportTask: vi.fn(() => of({
      task_id: 'voice-training-export-a', idempotent_replay: false,
    })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    api.getConsent.mockReturnValue(of(inactiveConsent));
    TestBed.configureTestingModule({
      imports: [VoicePersonalizationComponent],
      providers: [{ provide: VoiceApiService, useValue: api }],
    });
  });

  it('shows purpose, consent version and retention without opting in automatically', () => {
    const fixture = TestBed.createComponent(VoicePersonalizationComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('learningContext', context);
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('bewusste Hub-Aktionen');
    expect(text).toContain('Version 0');
    expect(text).toContain('Retention: 90 Tage');
    expect(text).toContain('schema-validierter Export und Import');
    expect(text).toContain('Nur Personalisierungs-Feedback zurücksetzen');
    expect(text).toContain('Vollständiges Voice-Profil löschen');
    expect(text).toContain('Export only');
    expect(api.setConsent).not.toHaveBeenCalled();
    expect(api.addPersonalizationFeedback).not.toHaveBeenCalled();
  });

  it('requires separate consent and concrete learning confirmations', () => {
    const fixture = TestBed.createComponent(VoicePersonalizationComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('learningContext', context);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    component.enableLearning();
    expect(api.setConsent).not.toHaveBeenCalled();
    component.consentAcknowledged = true;
    component.enableLearning();
    expect(api.setConsent).toHaveBeenCalledWith(
      'http://hub.test', 'profile-a',
      expect.objectContaining({ granted: true, retention_days: 90 }),
      expect.stringContaining('voice-ui:consent:grant:'),
    );

    component.addFeedback();
    expect(api.addPersonalizationFeedback).not.toHaveBeenCalled();
    component.learningConfirmed = true;
    component.feedbackKind = 'substitution';
    component.addFeedback();
    expect(api.addPersonalizationFeedback).toHaveBeenCalledWith(
      'http://hub.test',
      expect.objectContaining({
        profile_id: 'profile-a', review_id: 'review-a', source_text: 'An ander', target_text: 'Ananta',
      }),
      expect.any(String),
    );
  });

  it('requires confirmation before Hub reset and clears the local snapshot afterward', () => {
    api.getConsent.mockReturnValueOnce(of(activeConsent));
    const fixture = TestBed.createComponent(VoicePersonalizationComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('learningContext', context);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    expect(component.snapshot).toEqual(snapshot);

    component.resetData();
    expect(api.resetPersonalization).not.toHaveBeenCalled();
    component.resetConfirmed = true;
    component.resetData();
    expect(api.resetPersonalization).toHaveBeenCalledWith(
      'http://hub.test', 'profile-a', expect.stringContaining('voice-ui:personalization:reset:'),
    );
    expect(component.snapshot).toBeNull();
  });

  it('validates import JSON and requires consent plus explicit confirmation', () => {
    api.getConsent.mockReturnValueOnce(of(activeConsent));
    const fixture = TestBed.createComponent(VoicePersonalizationComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('learningContext', context);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    component.importJson = '{broken';
    component.importConfirmed = true;
    component.importData();
    expect(component.importErrorCode).toBe('voice_governance.invalid_json');
    expect(api.importPersonalization).not.toHaveBeenCalled();

    component.importJson = JSON.stringify({
      schema_version: 'voice-personalization.v1', profile_id: 'profile-a',
      items: [{ kind: 'vocabulary', target_text: 'Ananta', metadata: { language: 'de' } }],
    });
    component.importData();
    expect(api.importPersonalization).toHaveBeenCalledWith(
      'http://hub.test', 'profile-a',
      expect.objectContaining({ schema_version: 'voice-personalization.v1' }),
      expect.stringContaining('voice-ui:personalization:import:'),
    );
  });

  it('keeps feedback reset separate from complete privacy deletion', () => {
    api.getConsent.mockReturnValueOnce(of(activeConsent));
    const fixture = TestBed.createComponent(VoicePersonalizationComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('learningContext', context);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    component.profileDeleteConfirmed = true;
    component.deleteProfile();
    expect(api.deleteVoiceProfile).not.toHaveBeenCalled();
    component.profileDeleteConfirmationText = 'profile-a';
    component.deleteProfile();

    expect(api.deleteVoiceProfile).toHaveBeenCalledWith(
      'http://hub.test', 'profile-a', expect.stringContaining('voice-ui:privacy:delete-profile:'),
    );
    expect(api.resetPersonalization).not.toHaveBeenCalled();
    expect(component.consent?.granted).toBe(false);
    expect(component.successMessage).toContain('vollständig gelöscht');
    expect(component.successMessage).toContain('2 Streams widerrufen');
    expect(component.privacyDeletion?.runtime_cleanup_pending).toBe(false);
  });

  it('reports pending Runtime cleanup explicitly instead of claiming complete deletion', () => {
    api.getConsent.mockReturnValueOnce(of(activeConsent));
    api.deleteVoiceProfile.mockReturnValueOnce(of({
      profile_id: 'profile-a', deleted_count: 5, deleted_by_store: { feedback: 1 }, snapshots_revoked: true,
      revoked_stream_count: 3, runtime_cleanup_failed_count: 1, runtime_cleanup_pending: true,
    }));
    const fixture = TestBed.createComponent(VoicePersonalizationComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('learningContext', context);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    component.profileDeleteConfirmed = true;
    component.profileDeleteConfirmationText = 'profile-a';
    component.deleteProfile();
    fixture.detectChanges();

    expect(component.successMessage).toBe('');
    expect(component.errorCode).toBe('voice_privacy.runtime_cleanup_pending');
    expect(component.errorMessage).toContain('1 Runtime-Bereinigungen sind noch ausstehend');
    expect(component.errorMessage).not.toContain('vollständig gelöscht');
    const status = (fixture.nativeElement as HTMLElement)
      .querySelector('[data-testid="voice-privacy-delete-result"]');
    expect(status?.getAttribute('data-cleanup-state')).toBe('pending');
    expect(status?.textContent).toContain('Streams widerrufen: 3');
    expect(status?.textContent).toContain('Fehlgeschlagene Runtime-Bereinigungen: 1');
    expect(status?.querySelector('[data-reason-code]')?.getAttribute('data-reason-code'))
      .toBe('voice_privacy.runtime_cleanup_pending');
  });

  it('creates only an explicitly confirmed non-training export task', () => {
    api.getConsent.mockReturnValueOnce(of(activeConsent));
    const fixture = TestBed.createComponent(VoicePersonalizationComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('learningContext', context);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    component.createFineTuningExportTask();
    expect(api.createFineTuningExportTask).not.toHaveBeenCalled();
    component.fineTuningExportConfirmed = true;
    component.createFineTuningExportTask();
    fixture.detectChanges();

    expect(api.createFineTuningExportTask).toHaveBeenCalledWith(
      'http://hub.test', 'profile-a',
      expect.objectContaining({ confirmed: true }),
      expect.stringContaining('voice-ui:personalization:fine-tuning-export:'),
    );
    expect(component.fineTuningTask?.task_id).toBe('voice-training-export-a');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('startet kein Training');
  });
});
