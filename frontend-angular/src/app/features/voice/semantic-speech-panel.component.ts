import { AsyncPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SemanticSpeechSettings } from '../../services/semantic-speech-settings';
import { SemanticSpeechQualityMode } from '../../services/semantic-speech-quality-controller.service';

import {
  SpeechTranscriptDisplayState,
  SpeechCorrectionStatus,
  SpeechTranscriptRevisionStore,
} from '../../services/speech-transcript-revision.store';

export type SemanticSpeechPanelSettings = SemanticSpeechSettings;

export type SemanticSpeechTransportState = 'stopped' | 'starting' | 'active' | 'stopping' | 'failed';

@Component({
  selector: 'app-semantic-speech-panel',
  standalone: true,
  imports: [AsyncPipe, FormsModule],
  templateUrl: './semantic-speech-panel.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [`
    :host { display: block; container-type: inline-size; }
    fieldset, .transcript { border: 1px solid currentColor; border-radius: .5rem; padding: .75rem; }
    .controls { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 13rem), 1fr)); }
    label { display: grid; gap: .25rem; }
    button, select, input { min-height: 2.75rem; }
    button:focus-visible, select:focus-visible, input:focus-visible { outline: 3px solid currentColor; outline-offset: 2px; }
    ol { display: grid; gap: .5rem; padding-inline-start: 1.5rem; }
    li { overflow-wrap: anywhere; }
    .state { border: 1px solid currentColor; border-radius: 999px; display: inline-block; margin-inline-end: .5rem; padding: .1rem .45rem; }
    .correction_failed, .ordinary_fallback { border-style: dashed; }
    details { margin-block-start: .35rem; }
    @container (max-width: 28rem) { .controls { grid-template-columns: 1fr; } }
  `],
})
export class SemanticSpeechPanelComponent implements OnChanges {
  @Input() displayMode: SemanticSpeechPanelSettings['displayMode'] = 'live';
  @Input() segmentDurationSeconds = 60;
  @Input() correctEachSegment = true;
  @Input() paused = false;
  @Input() ordinaryAudioOverride = false;
  @Input() transportState: SemanticSpeechTransportState = 'stopped';
  @Input() transportReason = 'semantic_speech_not_started';
  @Input() qualityMode: SemanticSpeechQualityMode = 'ordinary_audio';
  @Input() qualityReason = 'quality_initial';
  @Input() canStartTransport = false;
  @Output() readonly settingsChange = new EventEmitter<SemanticSpeechPanelSettings>();
  @Output() readonly startRequested = new EventEmitter<void>();
  @Output() readonly stopRequested = new EventEmitter<void>();

  private readonly store = inject(SpeechTranscriptRevisionStore);
  readonly turns$ = this.store.turns$;

  ngOnChanges(): void {
    this.store.setLiveMode(this.displayMode === 'live');
  }

  setDisplayMode(value: string): void {
    if (value !== 'live' && value !== 'segment') return;
    this.displayMode = value;
    this.store.setLiveMode(value === 'live');
    this.emitSettings();
  }

  setSegmentDuration(value: unknown): void {
    const duration = Number(value);
    if (![10, 30, 60, 90, 120].includes(duration)) return;
    this.segmentDurationSeconds = duration;
    this.emitSettings();
  }

  togglePaused(): void {
    this.paused = !this.paused;
    this.emitSettings();
  }

  toggleCorrection(enabled: boolean): void {
    this.correctEachSegment = Boolean(enabled);
    this.emitSettings();
  }

  toggleOrdinaryAudio(enabled: boolean): void {
    this.ordinaryAudioOverride = Boolean(enabled);
    this.emitSettings();
  }

  label(state: SpeechTranscriptDisplayState): string {
    const labels: Record<SpeechTranscriptDisplayState, string> = {
      provisional: 'vorläufig',
      final: 'final',
      corrected: 'korrigiert',
      correction_failed: 'Korrektur fehlgeschlagen',
      ordinary_fallback: 'normales Audio aktiv',
    };
    return labels[state];
  }

  correctionLabel(status: SpeechCorrectionStatus): string {
    const labels: Record<SpeechCorrectionStatus, string> = {
      not_requested: 'noch nicht angefordert',
      awaiting_source: 'wartet auf verschlüsselte Source',
      pending: 'Korrektur läuft',
      completed: 'Korrektur abgeschlossen',
      failed: 'Korrektur fehlgeschlagen',
      missing_source: 'Source fehlt oder ist abgelaufen',
      disabled: 'Korrektur deaktiviert',
    };
    return labels[status];
  }

  requestStart(): void {
    if (!this.canStartTransport || !['stopped', 'failed'].includes(this.transportState)) return;
    this.startRequested.emit();
  }

  requestStop(): void {
    if (!['starting', 'active', 'failed'].includes(this.transportState)) return;
    this.stopRequested.emit();
  }

  private emitSettings(): void {
    this.settingsChange.emit(Object.freeze({
      displayMode: this.displayMode,
      segmentDurationSeconds: this.segmentDurationSeconds,
      correctEachSegment: this.correctEachSegment,
      paused: this.paused,
      ordinaryAudioOverride: this.ordinaryAudioOverride,
    }));
  }
}
