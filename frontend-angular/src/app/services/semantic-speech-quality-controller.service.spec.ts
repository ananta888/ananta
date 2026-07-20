import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import {
  SemanticSpeechQualityControllerService,
  SemanticSpeechQualityReport,
} from './semantic-speech-quality-controller.service';

function report(measuredAtMs = 10_000): SemanticSpeechQualityReport {
  return {
    measuredAtMs, lossRatio: 0, queueBytes: 0, partialAgeMs: 10, correctionLagMs: 20,
    sourceLossRatio: 0, featureLossRatio: 0, reconstructionErrorRatio: 0,
  };
}

describe('SemanticSpeechQualityControllerService', () => {
  let service: SemanticSpeechQualityControllerService;

  beforeEach(() => {
    TestBed.resetTestingModule();
    service = TestBed.inject(SemanticSpeechQualityControllerService);
  });

  it('applies fixed degradation thresholds and exposes ordinary audio throughout', () => {
    expect(service.ingest(report(), 'semantic_reconstruction')).toMatchObject({
      mode: 'semantic_reconstruction', semanticFeaturesEnabled: true, delayedSourceEnabled: true,
    });
    const decision = service.ingest({ ...report(15_000), correctionLagMs: 120_001 }, 'semantic_reconstruction');
    expect(decision).toMatchObject({
      mode: 'transcript_live', reasonCode: 'correction_lag_high', ordinaryAudioAvailable: true,
      semanticFeaturesEnabled: false, delayedSourceEnabled: false,
    });
  });

  it('holds normal mode flapping for five seconds', () => {
    expect(service.ingest(report(10_000), 'semantic_reconstruction').transitioned).toBe(true);
    const held = service.ingest({ ...report(12_000), featureLossRatio: 0.5 }, 'semantic_reconstruction');
    expect(held).toMatchObject({ mode: 'semantic_reconstruction', reasonCode: 'quality_hysteresis_hold' });
    expect(service.ingest({ ...report(15_000), featureLossRatio: 0.5 }, 'semantic_reconstruction').mode)
      .toBe('transcript_live');
  });

  it('honours revoke and user override immediately and has no timers', () => {
    service.ingest(report(), 'semantic_reconstruction');
    expect(service.ingest(report(10_001), 'semantic_reconstruction', { revoked: true }).mode).toBe('ordinary_audio');
    service.ingest(report(20_000), 'delayed_correction');
    expect(service.ingest(report(20_001), 'delayed_correction', { userOrdinaryOverride: true }).mode)
      .toBe('ordinary_audio');
    expect(service.snapshot().timers).toBe(0);
  });
});
