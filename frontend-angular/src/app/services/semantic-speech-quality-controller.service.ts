import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export type SemanticSpeechQualityMode =
  | 'ordinary_audio' | 'transcript_live' | 'semantic_reconstruction' | 'delayed_correction' | 'segment_only';

export interface SemanticSpeechQualityReport {
  measuredAtMs: number;
  lossRatio: number;
  queueBytes: number;
  partialAgeMs: number;
  correctionLagMs: number;
  sourceLossRatio: number;
  featureLossRatio: number;
  reconstructionErrorRatio: number;
}

export interface SemanticSpeechQualityState {
  mode: SemanticSpeechQualityMode;
  reasonCode: string;
  transitioned: boolean;
  ordinaryAudioAvailable: true;
  liveTranscriptEnabled: boolean;
  delayedSourceEnabled: boolean;
  semanticFeaturesEnabled: boolean;
}

const MIN_HOLD_MS = 5_000;

@Injectable({ providedIn: 'root' })
export class SemanticSpeechQualityControllerService {
  private mode: SemanticSpeechQualityMode = 'ordinary_audio';
  private lastTransitionMs: number | null = null;
  readonly state$ = new BehaviorSubject<SemanticSpeechQualityState>(this.state('quality_initial', false));

  ingest(
    report: SemanticSpeechQualityReport,
    desiredMode: SemanticSpeechQualityMode,
    options: { revoked?: boolean; userOrdinaryOverride?: boolean; semanticRuntimeFailed?: boolean } = {},
  ): SemanticSpeechQualityState {
    this.validate(report);
    if (options.revoked) return this.transition('ordinary_audio', 'consent_revoked', report.measuredAtMs, true);
    if (options.userOrdinaryOverride) {
      return this.transition('ordinary_audio', 'user_ordinary_override', report.measuredAtMs, true);
    }
    const [target, reason] = this.target(report, desiredMode, Boolean(options.semanticRuntimeFailed));
    return this.transition(target, reason, report.measuredAtMs, false);
  }

  containRuntimeFailure(reasonCode: string, nowMs = Date.now()): SemanticSpeechQualityState {
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(reasonCode)) reasonCode = 'semantic_runtime_failed';
    // The normal encrypted audio path is exposed immediately even while mode
    // hysteresis prevents UI/network flapping.
    const next = this.transition('ordinary_audio', reasonCode, nowMs, false);
    return { ...next, ordinaryAudioAvailable: true };
  }

  reset(reasonCode = 'quality_reset'): SemanticSpeechQualityState {
    this.mode = 'ordinary_audio';
    this.lastTransitionMs = null;
    const state = this.state(reasonCode, false);
    this.state$.next(state);
    return state;
  }

  snapshot(): Readonly<{ mode: SemanticSpeechQualityMode; lastTransitionMs: number | null; timers: number }> {
    return Object.freeze({ mode: this.mode, lastTransitionMs: this.lastTransitionMs, timers: 0 });
  }

  private target(
    report: SemanticSpeechQualityReport,
    desired: SemanticSpeechQualityMode,
    failed: boolean,
  ): [SemanticSpeechQualityMode, string] {
    if (failed) return ['ordinary_audio', 'semantic_runtime_failed'];
    if (report.lossRatio > 0.08) return ['ordinary_audio', 'packet_loss_high'];
    if (report.queueBytes > 3 * 1024 * 1024) return ['ordinary_audio', 'speech_queue_high'];
    if (report.partialAgeMs > 1_000) return ['ordinary_audio', 'live_partial_stale'];
    if (report.reconstructionErrorRatio > 0.20) return ['transcript_live', 'reconstruction_quality_low'];
    if (report.sourceLossRatio > 0.20) return ['transcript_live', 'source_loss_high'];
    if (report.correctionLagMs > 120_000) return ['transcript_live', 'correction_lag_high'];
    if (report.featureLossRatio > 0.25) return ['transcript_live', 'feature_loss_high'];
    return [desired, 'quality_healthy'];
  }

  private transition(
    target: SemanticSpeechQualityMode,
    reasonCode: string,
    nowMs: number,
    immediate: boolean,
  ): SemanticSpeechQualityState {
    let transitioned = false;
    if (target !== this.mode) {
      if (immediate || this.lastTransitionMs === null || nowMs - this.lastTransitionMs >= MIN_HOLD_MS) {
        this.mode = target;
        this.lastTransitionMs = nowMs;
        transitioned = true;
      } else {
        reasonCode = 'quality_hysteresis_hold';
      }
    }
    const state = this.state(reasonCode, transitioned);
    this.state$.next(state);
    return state;
  }

  private state(reasonCode: string, transitioned: boolean): SemanticSpeechQualityState {
    return Object.freeze({
      mode: this.mode,
      reasonCode,
      transitioned,
      ordinaryAudioAvailable: true,
      liveTranscriptEnabled: this.mode !== 'segment_only',
      delayedSourceEnabled: ['semantic_reconstruction', 'delayed_correction'].includes(this.mode),
      semanticFeaturesEnabled: this.mode === 'semantic_reconstruction',
    });
  }

  private validate(report: SemanticSpeechQualityReport): void {
    const ratios = [
      report.lossRatio, report.sourceLossRatio, report.featureLossRatio, report.reconstructionErrorRatio,
    ];
    const integers = [report.measuredAtMs, report.queueBytes, report.partialAgeMs, report.correctionLagMs];
    if (ratios.some(value => !Number.isFinite(value) || value < 0 || value > 1)) {
      throw new Error('speech_quality_ratio_invalid');
    }
    if (integers.some(value => !Number.isSafeInteger(value) || value < 0)) {
      throw new Error('speech_quality_measurement_invalid');
    }
  }
}
