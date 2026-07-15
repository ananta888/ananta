import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  Input,
  OnChanges,
  inject,
} from '@angular/core';
import { JsonPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { VoiceApiService } from './voice-api.service';
import { VoiceLearningContext } from './voice-candidate-review.component';
import { VOICE_LONG_RUN_RECOVERY, VoiceLongRunRecoveryPort } from './voice-long-run-recovery';
import { VOICE_LONG_RUN_SPOOL, VoiceLongRunSpoolPort } from './voice-long-run-spool';
import {
  VoiceConsent,
  VoiceConsentCategory,
  VoiceFineTuningExportTaskResult,
  VoicePersonalizationExport,
  VoicePersonalizationImportPayload,
  VoicePersonalizationSnapshot,
  VoicePrivacyDeletionResult,
} from './voice.models';
import { validatePersonalizationImport, voiceError, voiceMutationKey } from './voice-ui.helpers';

interface ConsentChoice {
  id: VoiceConsentCategory;
  label: string;
  purpose: string;
}

@Component({
  selector: 'app-voice-personalization',
  standalone: true,
  imports: [FormsModule, JsonPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './voice-personalization.component.html',
  styleUrl: './voice-settings.css',
})
export class VoicePersonalizationComponent implements OnChanges {
  @Input({ required: true }) hubUrl = '';
  @Input() learningContext: VoiceLearningContext | null = null;

  private readonly api = inject(VoiceApiService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly longRunSpool: VoiceLongRunSpoolPort = inject(VOICE_LONG_RUN_SPOOL);
  private readonly longRunRecovery: VoiceLongRunRecoveryPort = inject(VOICE_LONG_RUN_RECOVERY);

  readonly consentChoices: ConsentChoice[] = [
    { id: 'vocabulary', label: 'Wortschatz', purpose: 'Explizit bestätigte Begriffe bei späteren Transkriptionen berücksichtigen.' },
    { id: 'text_corrections', label: 'Textkorrekturen', purpose: 'Bestätigte Ersetzungen und Negativbeispiele berücksichtigen.' },
    { id: 'preferences', label: 'Präferenzen', purpose: 'Vom Nutzer bestätigte Schreibweisen bevorzugen.' },
    { id: 'audio_fingerprint', label: 'Audio-Fingerprint', purpose: 'Nur freigeben, wenn der Hub diese Kategorie für ein Profil anbietet.' },
  ];

  profileId = 'default';
  retentionDays = 90;
  selectedCategories: Record<VoiceConsentCategory, boolean> = {
    vocabulary: true,
    text_corrections: true,
    preferences: true,
    audio_fingerprint: false,
  };
  consentAcknowledged = false;
  revokeConfirmed = false;
  resetConfirmed = false;
  profileDeleteConfirmed = false;
  profileDeleteConfirmationText = '';
  learningConfirmed = false;
  feedbackKind: 'vocabulary' | 'substitution' | 'preference' | 'negative' = 'substitution';
  sourceText = '';
  targetText = '';
  consent: VoiceConsent | null = null;
  snapshot: VoicePersonalizationSnapshot | null = null;
  exported: VoicePersonalizationExport | null = null;
  importJson = '';
  importConfirmed = false;
  importErrorCode = '';
  importErrorMessage = '';
  fineTuningExportConfirmed = false;
  fineTuningPurpose = 'private voice spelling export';
  fineTuningLicense = 'user-provided-private-data';
  fineTuningTask: VoiceFineTuningExportTaskResult | null = null;
  privacyDeletion: VoicePrivacyDeletionResult | null = null;
  busy = false;
  errorCode = '';
  errorMessage = '';
  successMessage = '';
  private loadedProfileId = '';

  ngOnChanges(): void {
    if (this.learningContext) {
      this.profileId = this.learningContext.review.profile_id;
      this.sourceText = this.learningContext.sourceText;
      this.targetText = this.learningContext.targetText;
      this.learningConfirmed = false;
    }
    if (this.hubUrl && (!this.consent || this.loadedProfileId !== this.profileId.trim())) this.load();
  }

  load(): void {
    if (!this.profileId.trim()) return;
    this.beginRequest();
    this.api.getConsent(this.hubUrl, this.profileId.trim()).subscribe({
      next: (consent) => {
        this.consent = consent;
        this.loadedProfileId = consent.profile_id;
        this.retentionDays = consent.retention_days || this.retentionDays;
        if (consent.granted || consent.categories.length) {
          for (const choice of this.consentChoices) {
            this.selectedCategories[choice.id] = consent.categories.includes(choice.id);
          }
        }
        if (consent.granted) this.loadSnapshot();
        else {
          this.snapshot = null;
          this.busy = false;
          this.cdr.markForCheck();
        }
      },
      error: (error) => this.fail(error),
    });
  }

  enableLearning(): void {
    if (!this.consentAcknowledged || !this.validRetention()) return;
    const categories = this.categories();
    if (!categories.length) return;
    this.setConsent(true, categories, 'Personalisierung wurde bewusst aktiviert.');
  }

  revokeLearning(): void {
    if (!this.revokeConfirmed) return;
    this.setConsent(false, [], 'Consent wurde widerrufen. Vorhandene Daten können jetzt separat gelöscht werden.');
  }

  addFeedback(): void {
    const review = this.learningContext?.review;
    if (!review || !this.consent?.granted || !this.learningConfirmed || !this.validFeedback()) return;
    this.beginRequest();
    this.api.addPersonalizationFeedback(this.hubUrl, {
      profile_id: review.profile_id,
      review_id: review.id,
      kind: this.feedbackKind,
      source_text: this.sourceText.trim() || undefined,
      target_text: this.targetText.trim() || undefined,
      metadata: { reason_code: 'angular_manual_voice_review' },
    }, voiceMutationKey('personalization:feedback')).subscribe({
      next: () => {
        this.busy = false;
        this.learningConfirmed = false;
        this.successMessage = 'Bestätigtes Review-Feedback wurde im Hub gespeichert.';
        this.loadSnapshot();
      },
      error: (error) => this.fail(error),
    });
  }

  exportData(): void {
    if (!this.profileId.trim()) return;
    this.beginRequest();
    this.api.exportPersonalization(this.hubUrl, this.profileId.trim()).subscribe({
      next: (exported) => {
        this.exported = exported;
        this.busy = false;
        this.successMessage = `Export aus dem Hub geladen: ${exported.items.length} Einträge.`;
        this.downloadExport(exported);
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error),
    });
  }

  async selectImportFile(event: Event): Promise<void> {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      this.importErrorCode = 'voice_personalization.invalid_import_items';
      this.importErrorMessage = 'Die Importdatei darf höchstens 2 MB groß sein.';
      return;
    }
    this.importJson = await file.text();
    this.validateImport();
    this.cdr.markForCheck();
  }

  validateImport(): VoicePersonalizationImportPayload | null {
    const validation = validatePersonalizationImport(this.importJson, this.profileId.trim());
    this.importErrorCode = validation.error?.code || '';
    this.importErrorMessage = validation.error?.message || '';
    return validation.payload;
  }

  importData(): void {
    const payload = this.validateImport();
    if (!payload || !this.importConfirmed || !this.consent?.granted) return;
    this.beginRequest();
    this.api.importPersonalization(
      this.hubUrl,
      this.profileId.trim(),
      payload,
      voiceMutationKey('personalization:import'),
    ).subscribe({
      next: (result) => {
        this.busy = false;
        this.importConfirmed = false;
        this.successMessage = `${result.imported_count} schema-validierte Einträge wurden in den Hub importiert.`;
        this.loadSnapshot();
      },
      error: (error) => this.fail(error),
    });
  }

  resetData(): void {
    if (!this.resetConfirmed || !this.profileId.trim()) return;
    this.beginRequest();
    this.api.resetPersonalization(
      this.hubUrl,
      this.profileId.trim(),
      voiceMutationKey('personalization:reset'),
    ).subscribe({
      next: (result) => {
        this.snapshot = null;
        this.exported = null;
        this.busy = false;
        this.resetConfirmed = false;
        this.successMessage = `${result.deleted_count} Feedback-Datensätze im Hub zurückgesetzt. Consent und Reviews bleiben erhalten.`;
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error),
    });
  }

  deleteProfile(): void {
    const profileId = this.profileId.trim();
    if (!this.profileDeleteConfirmed || this.profileDeleteConfirmationText.trim() !== profileId || !profileId) return;
    // clearProfile publishes the same-document/cross-tab fence synchronously,
    // then queues the durable IndexedDB tombstone and ciphertext deletion. It
    // must start before HTTP: the Hub may apply DELETE even when its response
    // is lost.
    let localCleanup: Promise<unknown | null>;
    try {
      localCleanup = this.longRunSpool.clearProfile(profileId).then(
        () => null,
        (error) => error,
      );
    } catch (error) {
      localCleanup = Promise.resolve(error);
    }
    try {
      const recovery = this.longRunRecovery.load();
      if (recovery?.request.profile_id === profileId) {
        this.longRunRecovery.clear(recovery.runId || undefined);
      }
    } catch {
      // The durable profile tombstone remains authoritative if recovery
      // metadata storage is unavailable.
    }
    this.privacyDeletion = null;
    this.beginRequest();
    this.api.deleteVoiceProfile(
      this.hubUrl,
      profileId,
      voiceMutationKey('privacy:delete-profile'),
    ).subscribe({
      next: (result) => {
        this.privacyDeletion = result;
        this.consent = {
          id: null,
          profile_id: profileId,
          granted: false,
          categories: [],
          retention_days: null,
          version: 0,
        };
        this.snapshot = null;
        this.exported = null;
        this.fineTuningTask = null;
        this.profileDeleteConfirmed = false;
        this.profileDeleteConfirmationText = '';
        void this.finishProfileDeletion(result, localCleanup);
      },
      error: (error) => void this.finishFailedProfileDeletion(error, localCleanup),
    });
  }

  private async finishProfileDeletion(
    result: VoicePrivacyDeletionResult,
    localCleanup: Promise<unknown | null>,
  ): Promise<void> {
    const localCleanupError = await localCleanup;
    if (localCleanupError) {
      this.busy = false;
      this.successMessage = '';
      this.errorCode = 'voice_privacy.local_cleanup_failed';
      this.errorMessage = `${result.deleted_count} Hub-Profildatensätze wurden gelöscht, aber der lokale verschlüsselte Langzeit-Audiopuffer konnte nicht bereinigt werden: ${voiceError(localCleanupError).message}`;
      this.cdr.markForCheck();
      return;
    }
    this.busy = false;
    if (result.runtime_cleanup_pending) {
      this.successMessage = '';
      this.errorCode = 'voice_privacy.runtime_cleanup_pending';
      this.errorMessage = `${result.deleted_count} Hub-Profildatensätze gelöscht, aber ${result.runtime_cleanup_failed_count} Runtime-Bereinigungen sind noch ausstehend. Die Löschung ist noch nicht vollständig abgeschlossen.`;
    } else {
      this.successMessage = `${result.deleted_count} Voice-Profildatensätze vollständig gelöscht; ${result.revoked_stream_count} Streams widerrufen; Snapshots widerrufen: ${result.snapshots_revoked ? 'ja' : 'nein'}.`;
    }
    this.cdr.markForCheck();
  }

  private async finishFailedProfileDeletion(
    serverError: unknown,
    localCleanup: Promise<unknown | null>,
  ): Promise<void> {
    const localCleanupError = await localCleanup;
    this.fail(serverError);
    if (localCleanupError) {
      this.errorMessage = `${this.errorMessage} Die lokale Löschabsicht wurde signalisiert, aber der verschlüsselte Langzeit-Audiopuffer konnte nicht vollständig bereinigt werden: ${voiceError(localCleanupError).message}`;
      this.cdr.markForCheck();
    }
  }

  createFineTuningExportTask(): void {
    if (
      !this.fineTuningExportConfirmed
      || !this.consent?.granted
      || !this.fineTuningPurpose.trim()
      || !this.fineTuningLicense.trim()
    ) return;
    this.beginRequest();
    this.api.createFineTuningExportTask(this.hubUrl, this.profileId.trim(), {
      confirmed: true,
      purpose: this.fineTuningPurpose.trim(),
      license: this.fineTuningLicense.trim(),
    }, voiceMutationKey('personalization:fine-tuning-export')).subscribe({
      next: (task) => {
        this.fineTuningTask = task;
        this.busy = false;
        this.fineTuningExportConfirmed = false;
        this.successMessage = 'Der Hub-Task erzeugt ausschließlich einen minimierten Export und startet kein Training.';
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error),
    });
  }

  validRetention(): boolean {
    return Number.isInteger(this.retentionDays) && this.retentionDays >= 1 && this.retentionDays <= 3650;
  }

  validFeedback(): boolean {
    if (this.feedbackKind === 'vocabulary') return Boolean(this.targetText.trim());
    if (this.feedbackKind === 'negative') return Boolean(this.sourceText.trim());
    return Boolean(this.sourceText.trim() && this.targetText.trim());
  }

  categories(): VoiceConsentCategory[] {
    return this.consentChoices.filter((choice) => this.selectedCategories[choice.id]).map((choice) => choice.id);
  }

  private setConsent(granted: boolean, categories: VoiceConsentCategory[], success: string): void {
    this.beginRequest();
    this.api.setConsent(this.hubUrl, this.profileId.trim(), {
      granted,
      categories,
      retention_days: this.retentionDays,
    }, voiceMutationKey(`consent:${granted ? 'grant' : 'revoke'}`)).subscribe({
      next: (consent) => {
        this.consent = consent;
        this.busy = false;
        this.consentAcknowledged = false;
        this.revokeConfirmed = false;
        this.successMessage = success;
        if (consent.granted) this.loadSnapshot();
        else this.snapshot = null;
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error),
    });
  }

  private loadSnapshot(): void {
    this.api.getPersonalizationSnapshot(this.hubUrl, this.profileId.trim()).subscribe({
      next: (snapshot) => {
        this.snapshot = snapshot;
        this.busy = false;
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error),
    });
  }

  private downloadExport(exported: VoicePersonalizationExport): void {
    const blob = new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = `ananta-voice-personalization-${exported.profile_id}.json`;
    anchor.click();
    URL.revokeObjectURL(href);
  }

  private beginRequest(): void {
    this.busy = true;
    this.errorCode = '';
    this.errorMessage = '';
    this.successMessage = '';
  }

  private fail(error: unknown): void {
    const detail = voiceError(error);
    this.busy = false;
    this.errorCode = detail.code;
    this.errorMessage = detail.message;
    this.cdr.markForCheck();
  }
}
