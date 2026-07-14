import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  EventEmitter,
  Input,
  Output,
  inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { VoiceApiService } from './voice-api.service';
import {
  VoiceCandidate,
  VoiceReview,
  VoiceReviewDecision,
  VoiceTranscriptionResult,
} from './voice.models';
import { voiceError, voiceMutationKey } from './voice-ui.helpers';

export interface VoiceLearningContext {
  review: VoiceReview;
  sourceText: string;
  targetText: string;
}

@Component({
  selector: 'app-voice-candidate-review',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './voice-candidate-review.component.html',
  styleUrl: './voice-settings.css',
})
export class VoiceCandidateReviewComponent {
  @Input({ required: true }) hubUrl = '';
  @Input() hideTranscriptionInput = false;
  @Input() set embeddedProfileId(value: string) {
    if (value?.trim()) this.profileId = value.trim();
  }
  @Input() set embeddedSessionId(value: string) {
    this.sessionId = String(value || '').trim();
  }
  @Input() set embeddedResult(value: VoiceTranscriptionResult | null) {
    if (!value || value === this.result) return;
    this.result = value;
    this.review = null;
    this.selectedCandidateId = value.selected_candidate_id
      || value.candidates?.find((candidate) => candidate.status === 'succeeded')?.candidate_id
      || '';
    const metadata = value.generative_corrector || value.correction;
    const original = String(value.original_text || metadata?.original_text || '');
    const corrected = String(metadata?.corrected_text || metadata?.proposed_text || value.text || '');
    this.correctionText = original && corrected !== original ? corrected : '';
    this.errorCode = '';
    this.errorMessage = '';
    this.successMessage = '';
    this.learningContextChange.emit(null);
    this.cdr.markForCheck();
  }
  @Output() learningContextChange = new EventEmitter<VoiceLearningContext | null>();

  private readonly api = inject(VoiceApiService);
  private readonly cdr = inject(ChangeDetectorRef);

  profileId = 'default';
  sessionId = '';
  language = '';
  file: File | null = null;
  result: VoiceTranscriptionResult | null = null;
  review: VoiceReview | null = null;
  selectedCandidateId = '';
  correctionText = '';
  busy = false;
  errorCode = '';
  errorMessage = '';
  successMessage = '';

  selectFile(event: Event): void {
    this.file = (event.target as HTMLInputElement).files?.[0] || null;
    this.result = null;
    this.review = null;
    this.selectedCandidateId = '';
    this.correctionText = '';
    this.learningContextChange.emit(null);
  }

  transcribe(): void {
    if (!this.file) return;
    this.beginRequest();
    this.api.transcribe(this.hubUrl, {
      file: this.file,
      fileName: this.file.name,
      language: this.language,
      profileId: this.profileId,
      sessionId: this.sessionId,
      idempotencyKey: voiceMutationKey('transcribe'),
    }).subscribe({
      next: (result) => {
        this.result = result;
        this.selectedCandidateId = result.selected_candidate_id
          || this.successfulCandidates()[0]?.candidate_id
          || '';
        this.busy = false;
        this.successMessage = 'Transkription abgeschlossen. Original-Candidates bleiben für die Review sichtbar.';
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error),
    });
  }

  successfulCandidates(): VoiceCandidate[] {
    return (this.result?.candidates || []).filter((candidate) => candidate.status === 'succeeded');
  }

  resultRef(): string {
    return String(this.result?.result_ref || this.result?.audit_id || '').trim();
  }

  canCreateReview(): boolean {
    return Boolean(this.profileId.trim() && this.resultRef() && this.successfulCandidates().length);
  }

  createReview(): void {
    if (!this.canCreateReview()) return;
    this.beginRequest();
    this.api.createReview(this.hubUrl, {
      profile_id: this.profileId.trim(),
      session_id: this.sessionId.trim() || undefined,
      result_ref: this.resultRef(),
      candidate_ids: this.successfulCandidates().map((candidate) => candidate.candidate_id),
    }, voiceMutationKey('review:create')).subscribe({
      next: (review) => {
        this.review = review;
        this.busy = false;
        this.successMessage = 'Manuelle Review im Hub angelegt.';
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error),
    });
  }

  decide(decision: VoiceReviewDecision): void {
    if (!this.review || this.review.state !== 'pending') return;
    if (decision === 'accept' && !this.selectedCandidateId) return;
    if (decision === 'correct' && !this.correctionText.trim()) return;
    this.beginRequest();
    this.api.decideReview(this.hubUrl, this.review.id, {
      decision,
      expected_version: this.review.version,
      selected_candidate_id: decision === 'reject' ? undefined : this.selectedCandidateId || undefined,
      correction_text: decision === 'correct' ? this.correctionText.trim() : undefined,
    }, voiceMutationKey(`review:${decision}`)).subscribe({
      next: (review) => {
        this.review = review;
        this.busy = false;
        this.successMessage = `Review abgeschlossen: ${review.state}. Lernen bleibt separat deaktiviert.`;
        this.emitLearningContext(review);
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error),
    });
  }

  candidate(id: string | null | undefined): VoiceCandidate | undefined {
    return (this.result?.candidates || []).find((entry) => entry.candidate_id === id);
  }

  formatTime(milliseconds: number | null | undefined): string {
    if (milliseconds == null) return '–';
    return `${(milliseconds / 1000).toFixed(2)} s`;
  }

  confidence(value: number | null | undefined): string {
    if (value == null) return '–';
    return `${Math.round(value * 100)} %`;
  }

  lineage(candidate: VoiceCandidate): string {
    return candidate.lineage_id || candidate.candidate_id;
  }

  parents(candidate: VoiceCandidate): string {
    const parents = candidate.parent_candidate_ids || [];
    return parents.length ? parents.join(' → ') : 'keine (Original)';
  }

  disagreementSource(candidateId: string): string {
    const candidate = this.candidate(candidateId);
    if (!candidate) return `Candidate ${candidateId}`;
    return [
      candidate.backend,
      `lineage ${this.lineage(candidate)}`,
      `audio ${candidate.audio_variant_id || 'original'}`,
      candidate.source_audio_digest || 'digest –',
      candidate.execution_location || 'voice-runtime',
      candidate.device || 'device –',
    ].join(' · ');
  }

  private emitLearningContext(review: VoiceReview): void {
    if (!['accepted', 'corrected'].includes(review.state)) {
      this.learningContextChange.emit(null);
      return;
    }
    const selected = this.candidate(review.selected_candidate_id);
    this.learningContextChange.emit({
      review,
      sourceText: selected?.text || '',
      targetText: review.correction_text || selected?.text || '',
    });
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
