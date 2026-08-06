export interface RenderNodePosition {
  readonly x: number;
  readonly y: number;
  readonly z: number;
  /** Fixed positions keep deterministic layouts stable during the force simulation. */
  readonly fixed: boolean;
}

export interface RenderGraphNode {
  readonly id: string;
  readonly label: string;
  readonly kind: string;
  readonly tooltip: string;
  readonly position?: Readonly<RenderNodePosition>;
}

export interface RenderGraphEdge {
  readonly id: string;
  readonly sourceId: string;
  readonly targetId: string;
  readonly kind: string;
  readonly label: string;
  readonly tooltip: string;
}

export interface RenderHighlightFactors {
  readonly hover: number;
  readonly selected: number;
  readonly connected: number;
}

export interface RenderNodeStyle {
  readonly color: string;
  readonly size: number;
  readonly opacity?: number;
  readonly highlightFactors?: Readonly<RenderHighlightFactors>;
}

export interface RenderEdgeStyle {
  readonly color: string;
  readonly width: number;
  readonly opacity?: number;
  readonly highlightFactors?: Readonly<RenderHighlightFactors>;
}

export interface RenderGraph {
  readonly nodes: readonly RenderGraphNode[];
  readonly edges: readonly RenderGraphEdge[];
}

export type RenderLimitStrategy = 'none' | 'focus' | 'reject';

export interface RenderLimitExceeded {
  readonly nodeCount: number;
  readonly edgeCount: number;
  readonly nodeLimit: number | null;
  readonly edgeLimit: number | null;
}

export const DEFAULT_RENDER_HIGHLIGHT_FACTORS: Readonly<RenderHighlightFactors> = Object.freeze({
  hover: 1.2,
  selected: 1.5,
  connected: 1.1,
});
