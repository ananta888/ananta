export type ModelAnalysisViewState =
  | 'loading'
  | 'empty'
  | 'unsupported'
  | 'permission'
  | 'error'
  | 'ready';

export type ModelAnalysisSectionStatus =
  | 'available'
  | 'unsupported'
  | 'not_run'
  | 'failed';

export type ModelAnalysisJobStatus =
  | 'queued'
  | 'claimed'
  | 'running'
  | 'cancel_requested'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'unknown';

export interface ModelAnalysisCapabilities {
  supported: boolean;
  reason_code?: string;
  max_graph_nodes: number;
  max_graph_edges: number;
}

export interface StartModelAnalysisRequest {
  import_ref: string;
  analysis_kind: 'full';
  profile_id: 'bounded-ui';
  requested_artifact_kinds: readonly ['report', 'model_graph'];
}

export interface ModelAnalysisJob {
  schema?: string;
  job_id: string;
  hub_task_id?: string;
  model_id: string;
  import_ref?: string;
  analysis_kind: string;
  profile_id: string;
  request_sha256?: string;
  requested_artifact_kinds: readonly string[];
  max_runtime_seconds?: number;
  max_output_bytes?: number;
  status: ModelAnalysisJobStatus;
  progress_percent: number;
  reason_code?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ModelAnalysisJobPage {
  items: readonly ModelAnalysisJob[];
  next_cursor: string | null;
}

export interface ModelAnalysisReportSection {
  name: string;
  status: ModelAnalysisSectionStatus;
  reason_code?: string;
  data: unknown;
}

export interface ModelAnalysisReport {
  schema: string;
  content_digest: string;
  sections: readonly ModelAnalysisReportSection[];
}

export interface ModelAnalysisGraphNode {
  node_id: string;
  label: string;
  kind: string;
}

export interface ModelAnalysisGraphEdge {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
  kind: string;
}

export interface ModelAnalysisGraph {
  schema: string;
  nodes: readonly ModelAnalysisGraphNode[];
  edges: readonly ModelAnalysisGraphEdge[];
  truncated: boolean;
}
