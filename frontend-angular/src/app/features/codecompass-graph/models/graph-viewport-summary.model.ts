export interface GraphViewportNodeCounts {
  readonly visible: number;
  readonly loaded: number;
  /** Null means that no valid structured total-count evidence was supplied. */
  readonly total: number | null;
}

export interface GraphViewportEdgeCounts extends GraphViewportNodeCounts {
  /** Edges internal to the loaded node window before a server-side edge cap. */
  readonly internalWindow: number | null;
}

export interface GraphViewportInventoryCounts {
  readonly visible: number;
  readonly loaded: number;
}

export type GraphEvidenceClassification =
  | 'complete'
  | 'partial'
  | 'unavailable'
  | 'unknown';

export type GraphProjectionIssueCode =
  | 'graph_node_window_bounded'
  | 'graph_edge_window_capped'
  | 'graph_relations_unresolved'
  | 'graph_window_evidence_inconsistent';

export type GraphSemanticIssueCode =
  | 'semantic_translation_unavailable'
  | 'semantic_graph_truncated'
  | 'semantic_graph_candidate_relations_truncated'
  | 'semantic_graph_relations_unresolved'
  | 'semantic_graph_degraded';

export type GraphArtifactIssueCode = 'graph_artifact_unavailable';

export interface GraphViewportIssue<TCode extends string> {
  readonly code: TCode;
  readonly affectedCount: number | null;
  readonly reasonCode: string | null;
}

export type GraphProjectionIssue = GraphViewportIssue<GraphProjectionIssueCode>;
export type GraphSemanticIssue = GraphViewportIssue<GraphSemanticIssueCode>;
export type GraphArtifactIssue = GraphViewportIssue<GraphArtifactIssueCode>;

/**
 * Renderer-independent summary of the currently visible graph viewport.
 * All totals and completeness states are evidence-backed; unknown stays null or
 * `unknown` instead of being inferred from warning prose.
 */
export interface GraphViewportSummary {
  readonly nodes: Readonly<GraphViewportNodeCounts>;
  readonly edges: Readonly<GraphViewportEdgeCounts>;
  /** Selected server scope before the visualization window was applied. */
  readonly scopeNodeTotal?: number | null;
  readonly scopeBoundaryEdges?: number | null;
  /** Unresolved records retained in a complete staged server scope. */
  readonly scopeUnresolvedEdges?: number | null;
  readonly domains: Readonly<GraphViewportInventoryCounts>;
  readonly rawRelationTypes: Readonly<GraphViewportInventoryCounts>;
  readonly nodeWindowBounded: boolean | null;
  readonly edgeWindowCapped: boolean | null;
  readonly semanticState: GraphEvidenceClassification;
  readonly artifactState: GraphEvidenceClassification;
  readonly projectionIssues: readonly Readonly<GraphProjectionIssue>[];
  readonly semanticIssues: readonly Readonly<GraphSemanticIssue>[];
  readonly artifactIssues: readonly Readonly<GraphArtifactIssue>[];
  readonly rawWarnings: readonly string[];
}
