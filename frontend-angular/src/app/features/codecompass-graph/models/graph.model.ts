// Canonical frontend model for CodeCompass graph data.
// Maps from domain_graph_artifact.v1 JSON (see schemas/artifacts/domain_graph_artifact.v1.json).
// Backend field mapping: node_id→id, node_type→kind, relation→edgeType, attributes.confidence→confidence.

export type GraphNodeKind =
  | 'java_method'
  | 'java_type'
  | 'config'
  | 'xml_tag'
  | 'unknown';

export type GraphEdgeType =
  | 'calls_probable_target'
  | 'injects_dependency'
  | 'field_type_uses'
  | 'extends'
  | 'implements'
  | 'child_of_type'
  | 'child_of_file'
  | 'declares_method'
  | 'declares_bean'
  | 'transactional_boundary'
  | 'jpa_relation'
  | 'related';

export interface GraphNode {
  id: string;
  kind: GraphNodeKind;
  label: string;
  file: string;
  content: string;
  recordId: string;
  metadata: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  edgeType: GraphEdgeType;
  confidence: number;
  metadata: Record<string, unknown>;
}

export interface GraphMetadata {
  sourceRef: string;
  sourceKind: string;
  nodeCount: number;
  edgeCount: number;
  [key: string]: unknown;
}

export interface GenericGraphModel {
  nodes: GraphNode[];
  edges: GraphEdge[];
  metadata: GraphMetadata;
  warnings: string[];
}
