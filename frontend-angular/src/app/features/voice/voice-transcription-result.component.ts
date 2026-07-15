import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

import {
  VoiceCorrectionEdit,
  VoiceCorrectionMetadata,
  VoiceTranscriptionResult,
} from './voice.models';
import { VoiceDiffChunk, voiceTranscriptDiff } from './voice-transcript-diff';

@Component({
  selector: 'app-voice-transcription-result',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './voice-transcription-result.component.html',
  styleUrls: ['./voice-settings.css', './voice-transcription-result.component.css'],
})
export class VoiceTranscriptionResultComponent {
  @Input({ required: true }) result!: VoiceTranscriptionResult;
  @Input() fallbackBackend = '';

  private readonly diffCache = new WeakMap<VoiceTranscriptionResult, VoiceDiffChunk[]>();

  originalText(): string {
    const correction = this.correction();
    return String(this.result.original_text || correction?.original_text
      || this.result.candidates?.find((candidate) => candidate.backend === 'vosk' && candidate.status === 'succeeded')?.text
      || this.result.text || '');
  }

  correctedText(): string {
    const correction = this.correction();
    return String(correction?.corrected_text || correction?.proposed_text || this.result.text || '');
  }

  correction(): VoiceCorrectionMetadata | null {
    const direct = this.result.generative_corrector || this.result.correction;
    if (direct) return direct;
    const provenance = this.result.provenance || {};
    const nested = provenance['generative_corrector'] || provenance['correction'];
    return nested && typeof nested === 'object' ? nested as VoiceCorrectionMetadata : null;
  }

  correctionModel(): string {
    const correction = this.correction();
    if (!correction) return 'keine generative Korrektur';
    const model = correction.model_id || correction.model;
    const revision = correction?.model_revision;
    return [model, revision].filter(Boolean).join(' · ') || 'keine generative Korrektur';
  }

  correctionEdits(): VoiceCorrectionEdit[] {
    return this.correction()?.edits || [];
  }

  correctionStatus(): string {
    const correction = this.correction();
    return String(correction?.['status'] || (correction ? 'completed' : 'not_requested'));
  }

  correctionReason(): string {
    return String(this.correction()?.['reason_code'] || '');
  }

  transcriptDiff(): VoiceDiffChunk[] {
    const cached = this.diffCache.get(this.result);
    if (cached) return cached;
    const diff = voiceTranscriptDiff(this.originalText(), this.correctedText());
    this.diffCache.set(this.result, diff);
    return diff;
  }
}
