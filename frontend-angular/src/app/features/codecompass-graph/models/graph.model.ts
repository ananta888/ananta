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
  | 'wiki_article'
  | 'wiki_section'
  | 'wiki_chunk'
  | 'unknown';

export type GraphEdgeType =
  | 'bean_factory_method'
  | 'calls_probable_target'
  | 'child_of_type'
  | 'child_of_file'
  | 'contains_entry'
  | 'contains_method'
  | 'contains_section'
  | 'contains_symbol'
  | 'contains_type'
  | 'controller_endpoint_declares'
  | 'declares_constructor'
  | 'declares_method'
  | 'declares_bean'
  | 'extends'
  | 'field_type_uses'
  | 'generic_type_uses'
  | 'implements'
  | 'imports_module'
  | 'imports_symbol'
  | 'injects_dependency'
  | 'transactional_boundary'
  | 'jpa_relation'
  | 'method_param_type_uses'
  | 'method_return_type_uses'
  | 'parent_child'
  | 'returns'
  | 'uses_type'
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
