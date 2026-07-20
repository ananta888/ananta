import { parseSemanticScene, SemanticScene } from './semantic-scene-model';
import {
  BoundedGridGeometryTracker,
  GeometryFeatureGrid,
  GeometryTracker,
  TrackedGeometry,
} from './semantic-heuristics/geometry-tracker';
import {
  AbsoluteGridChangeDetector,
  ChangeDetector,
  ChangeSummary,
} from './semantic-heuristics/change-detector';

export interface MaskedHeuristicInput {
  readonly masked: true;
  readonly width: number;
  readonly height: number;
  readonly luma: Uint8Array;
}

export interface DisposableFeatures extends GeometryFeatureGrid { close(): void }

export interface SemanticFeatureExtractor {
  readonly algorithmVersion: string;
  extract(input: MaskedHeuristicInput, signal: AbortSignal): Promise<DisposableFeatures>;
}

export interface SemanticBudgetPolicy {
  readonly version: string;
  allow(stage: string, elapsedMs: number, workingBytes: number): boolean;
}

export interface SemanticModelProposalAdapter {
  readonly algorithmVersion: string;
  propose(input: GeometryFeatureGrid, signal: AbortSignal): Promise<unknown>;
}

export interface HeuristicStageMetric {
  readonly stage: 'extract' | 'track' | 'change' | 'model_proposal';
  readonly runtimeMs: number;
  readonly inputBytes: number;
  readonly outputBytes: number;
  readonly confidence: number;
  readonly abortReason: string | null;
  readonly algorithmVersion: string;
}

export interface HeuristicPipelineResult {
  readonly regions: readonly TrackedGeometry[];
  readonly change: ChangeSummary;
  readonly modelProposal?: SemanticScene;
  readonly metrics: readonly HeuristicStageMetric[];
  readonly fallbackReason: string | null;
}

export interface HeuristicPipelineRequest {
  readonly input: MaskedHeuristicInput;
  readonly previous?: GeometryFeatureGrid;
  readonly deadlineMs: number;
  readonly signal?: AbortSignal;
}

class CoarseLumaExtractor implements SemanticFeatureExtractor {
  readonly algorithmVersion = 'coarse-luma/1.0.0';
  async extract(input: MaskedHeuristicInput): Promise<DisposableFeatures> {
    if (input.masked !== true || input.width < 1 || input.height < 1
        || input.luma.byteLength !== input.width * input.height) throw new Error('invalid_masked_input');
    const columns = Math.min(32, input.width);
    const rows = Math.min(32, input.height);
    const activity = new Uint8Array(columns * rows);
    for (let y = 0; y < rows; y += 1) for (let x = 0; x < columns; x += 1) {
      const sourceX = Math.floor(x * input.width / columns);
      const sourceY = Math.floor(y * input.height / rows);
      activity[y * columns + x] = input.luma[sourceY * input.width + sourceX];
    }
    return { columns, rows, activity, close: () => activity.fill(0) };
  }
}

class FixedVisualBudgetPolicy implements SemanticBudgetPolicy {
  readonly version = 'semantic-heuristic-budget/1.0.0';
  constructor(private readonly maxRuntimeMs = 24, private readonly maxWorkingBytes = 4 * 1024 * 1024) {}
  allow(_stage: string, elapsedMs: number, workingBytes: number): boolean {
    return elapsedMs <= this.maxRuntimeMs && workingBytes <= this.maxWorkingBytes;
  }
}

export class SemanticHeuristicPipelineService {
  constructor(
    private readonly extractor: SemanticFeatureExtractor = new CoarseLumaExtractor(),
    private readonly tracker: GeometryTracker = new BoundedGridGeometryTracker(),
    private readonly detector: ChangeDetector = new AbsoluteGridChangeDetector(),
    private readonly budget: SemanticBudgetPolicy = new FixedVisualBudgetPolicy(),
    private readonly clock: () => number = () => performance.now(),
  ) {}

  async run(request: HeuristicPipelineRequest, model?: SemanticModelProposalAdapter): Promise<HeuristicPipelineResult> {
    const started = this.clock();
    const metrics: HeuristicStageMetric[] = [];
    let features: DisposableFeatures | undefined;
    const stopped = (stage: string): string | null => {
      if (request.signal?.aborted) return 'cancelled';
      if (!Number.isFinite(request.deadlineMs) || this.clock() > request.deadlineMs) return 'deadline_exceeded';
      const bytes = features?.activity.byteLength ?? request.input.luma.byteLength;
      if (!this.budget.allow(stage, this.clock() - started, bytes)) return 'resource_budget_exceeded';
      return null;
    };
    const metric = (
      stage: HeuristicStageMetric['stage'], at: number, inputBytes: number, outputBytes: number,
      confidence: number, abortReason: string | null, algorithmVersion: string,
    ): void => {
      metrics.push(Object.freeze({
        stage, runtimeMs: Math.max(0, this.clock() - at), inputBytes, outputBytes,
        confidence, abortReason, algorithmVersion,
      }));
    };
    try {
      let reason = stopped('extract');
      if (reason) return emptyResult(metrics, reason);
      let at = this.clock();
      features = await this.extractor.extract(request.input, request.signal ?? new AbortController().signal);
      metric('extract', at, request.input.luma.byteLength, features.activity.byteLength, 1, null, this.extractor.algorithmVersion);
      reason = stopped('track');
      if (reason) {
        metric('track', this.clock(), features.activity.byteLength, 0, 0, reason, this.tracker.algorithmVersion);
        return emptyResult(metrics, reason);
      }
      at = this.clock();
      const regions = this.tracker.track(features);
      const regionConfidence = regions.length ? regions.reduce((sum, item) => sum + item.confidence, 0) / regions.length : 0;
      metric('track', at, features.activity.byteLength, regions.length * 40, regionConfidence, null, this.tracker.algorithmVersion);
      reason = stopped('change');
      if (reason) {
        metric('change', this.clock(), features.activity.byteLength, 0, 0, reason, this.detector.algorithmVersion);
        return { ...emptyResult(metrics, reason), regions };
      }
      at = this.clock();
      const change = this.detector.detect(request.previous, features);
      metric('change', at, features.activity.byteLength, 32, 1 - Math.min(1, change.changedRatio), null, this.detector.algorithmVersion);
      let proposal: SemanticScene | undefined;
      if (model) {
        reason = stopped('model_proposal');
        at = this.clock();
        if (!reason) {
          try {
            proposal = parseSemanticScene(await model.propose(features, request.signal ?? new AbortController().signal));
          } catch {
            reason = 'invalid_model_proposal';
          }
        }
        metric('model_proposal', at, features.activity.byteLength, proposal ? JSON.stringify(proposal).length : 0,
          proposal ? 1 : 0, reason, model.algorithmVersion);
      }
      return Object.freeze({ regions, change, modelProposal: proposal, metrics: Object.freeze(metrics), fallbackReason: reason });
    } finally {
      features?.close();
    }
  }
}

function emptyResult(metrics: readonly HeuristicStageMetric[], reason: string): HeuristicPipelineResult {
  return Object.freeze({
    regions: Object.freeze([]),
    change: Object.freeze({ changedCells: 0, changedRatio: 1, meanDelta: 255, classification: 'unknown' as const }),
    metrics: Object.freeze(metrics),
    fallbackReason: reason,
  });
}
