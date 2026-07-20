import {
  DisposableFeatures,
  SemanticBudgetPolicy,
  SemanticFeatureExtractor,
  SemanticHeuristicPipelineService,
} from './semantic-heuristic-pipeline.service';
import { BoundedGridGeometryTracker } from './semantic-heuristics/geometry-tracker';
import { AbsoluteGridChangeDetector } from './semantic-heuristics/change-detector';

class Extractor implements SemanticFeatureExtractor {
  readonly algorithmVersion = 'test-extractor/1';
  closed = 0;
  async extract(): Promise<DisposableFeatures> {
    return { columns: 2, rows: 2, activity: new Uint8Array([0, 255, 255, 0]), close: () => { this.closed += 1; } };
  }
}

describe('SemanticHeuristicPipelineService', () => {
  it('composes deterministic model-free strategies and reports every stage', async () => {
    const extractor = new Extractor();
    const service = new SemanticHeuristicPipelineService(
      extractor, new BoundedGridGeometryTracker(), new AbsoluteGridChangeDetector(),
      { version: 'budget/1', allow: () => true }, () => 10,
    );
    const result = await service.run({
      input: { masked: true, width: 2, height: 2, luma: new Uint8Array(4) },
      previous: { columns: 2, rows: 2, activity: new Uint8Array(4) }, deadlineMs: 20,
    });
    expect(result.regions.length).toBe(2);
    expect(result.metrics.map(item => item.stage)).toEqual(['extract', 'track', 'change']);
    expect(result.metrics.every(item => item.algorithmVersion.length > 0)).toBe(true);
    expect(extractor.closed).toBe(1);
  });

  it('aborts late stages reproducibly at deadline and releases features', async () => {
    const extractor = new Extractor();
    let now = 0;
    const service = new SemanticHeuristicPipelineService(
      extractor, new BoundedGridGeometryTracker(), new AbsoluteGridChangeDetector(),
      { version: 'budget/1', allow: () => true }, () => ++now,
    );
    const result = await service.run({
      input: { masked: true, width: 2, height: 2, luma: new Uint8Array(4) }, deadlineMs: 3,
    });
    expect(result.fallbackReason).toBe('deadline_exceeded');
    expect(result.metrics.at(-1)?.abortReason).toBe('deadline_exceeded');
    expect(extractor.closed).toBe(1);
  });

  it('rejects unbounded/invalid model output without granting authority or transport access', async () => {
    const extractor = new Extractor();
    const budget: SemanticBudgetPolicy = { version: 'budget/1', allow: () => true };
    const service = new SemanticHeuristicPipelineService(
      extractor, new BoundedGridGeometryTracker(), new AbsoluteGridChangeDetector(), budget, () => 1,
    );
    const result = await service.run({
      input: { masked: true, width: 2, height: 2, luma: new Uint8Array(4) }, deadlineMs: 2,
    }, { algorithmVersion: 'model-proposal/1', propose: async () => ({ inventedAuthority: true }) });
    expect(result.modelProposal).toBeUndefined();
    expect(result.fallbackReason).toBe('invalid_model_proposal');
    expect(result.metrics.at(-1)?.abortReason).toBe('invalid_model_proposal');
  });
});
