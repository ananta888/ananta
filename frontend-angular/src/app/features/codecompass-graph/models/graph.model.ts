// Canonical frontend model for CodeCompass graph data.
// Maps from domain_graph_artifact.v1 JSON (see schemas/artifacts/domain_graph_artifact.v1.json).
// Backend field mapping: node_id→id, node_type→kind, relation→edgeType, attributes.confidence→confidence.

export type GraphNodeKind =
  | 'java_constructor'
  | 'java_constructor_detail'
  | 'java_file'
  | 'java_method'
  | 'java_method_detail'
  | 'java_module_summary'
  | 'java_type'
  | 'md_file'
  | 'md_section'
  | 'properties_entry'
  | 'properties_file'
  | 'python_class'
  | 'python_file'
  | 'python_function'
  | 'python_import'
  | 'python_method'
  | 'python_module_summary'
  | 'typescript_class'
  | 'typescript_const'
  | 'typescript_constructor'
  | 'typescript_enum'
  | 'typescript_file'
  | 'typescript_folder_summary'
  | 'typescript_function'
  | 'typescript_import'
  | 'typescript_interface'
  | 'typescript_method'
  | 'typescript_type'
  | 'xml_file'
  | 'xml_node_detail'
  | 'xml_tag'
  | 'yaml_entry'
  | 'yaml_file'
  | 'config'
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
  | 'parent_child'
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
