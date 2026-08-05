// Canonical frontend model for CodeCompass graph data.
// Maps from domain_graph_artifact.v1 JSON (see schemas/artifacts/domain_graph_artifact.v1.json).
// Backend field mapping: node_id→id, node_type→kind, relation→edgeType, attributes.confidence→confidence.

import type {
  GraphMetricCapability,
  GraphMetricVector,
} from './graph-visual-metrics.model';

/** Canonical vocabulary emitted by codecompass_semantic_translation_graph.v1. */
export const SEMANTIC_TRANSLATION_NODE_KINDS = Object.freeze([
  'syntax_node',
  'semantic_node',
  'type_node',
  'symbol_node',
  'control_flow_node',
  'data_flow_node',
  'effect_node',
  'contract_node',
  'equivalence_rule',
  'transform_artifact',
] as const);

export const SEMANTIC_TRANSLATION_EDGE_TYPES = Object.freeze([
  'declares',
  'extends',
  'implements',
  'imports',
  'exports',
  'references',
  'uses',
  'calls',
  'reads',
  'writes',
  'returns',
  'throws',
  'maps_to',
  'equivalent_to',
  'requires',
  'ensures',
  'generated_by',
  'verified_by',
] as const);

export type SemanticTranslationNodeKind = typeof SEMANTIC_TRANSLATION_NODE_KINDS[number];
export type SemanticTranslationEdgeType = typeof SEMANTIC_TRANSLATION_EDGE_TYPES[number];

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
  | 'package_manager'
  | 'external_package'
  | 'buildable_component'
  | 'repository'
  | 'directory'
  | 'source_file'
  | 'aggregator'
  | 'runner'
  | 'test'
  | SemanticTranslationNodeKind
  | 'unknown';

export type GraphEdgeType =
  | 'bean_factory_method'
  | 'calls_probable_target'
  | 'child_of_type'
  | 'child_of_file'
  | 'contains_entry'
  | 'contains_directory'
  | 'contains_file'
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
  | 'frontend_guard_refs_field'
  | 'generic_type_uses'
  | 'implements'
  | 'imports_module'
  | 'imports_symbol'
  | 'injects_dependency'
  | 'transactional_boundary'
  | 'jpa_relation'
  | 'mapper_maps_type'
  | 'method_param_type_uses'
  | 'method_return_type_uses'
  | 'parent_child'
  | 'permission_checks_field'
  | 'policy_applies_to_field'
  | 'returns'
  | 'uses_type'
  | 'related'
  | 'wiki_link'
  | 'depends_on'
  | 'aggregates'
  | 'built_by'
  | 'tested_by'
  | 'test_calls_endpoint'
  | 'test_targets_type'
  | 'runs'
  | 'covers'
  | 'service_uses_repository'
  | SemanticTranslationEdgeType;

export type GraphDomainIdentitySource = 'domain_id' | 'domain_path' | 'file' | 'unassigned';

export interface GraphDomainIdentity {
  canonicalId: string;
  label: string;
  source: GraphDomainIdentitySource;
}

export interface GraphNode {
  id: string;
  kind: GraphNodeKind;
  /** Original backend value. It must not be replaced by the visual fallback kind. */
  rawNodeType?: string;
  /** Null means that rawNodeType has no registered semantic interpretation. */
  knownKind?: GraphNodeKind | null;
  label: string;
  file: string;
  content: string;
  recordId: string;
  domainId?: string;
  domainPath?: string;
  metrics?: GraphMetricVector;
  metadata: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  edgeType: GraphEdgeType;
  /** Original backend value. It must not be replaced by the visual fallback relation. */
  rawEdgeType?: string;
  /** Null means that rawEdgeType has no registered semantic interpretation. */
  knownRelation?: GraphEdgeType | null;
  confidence: number;
  multiplicity?: number;
  directed?: boolean;
  selfLoop?: boolean;
  metrics?: GraphMetricVector;
  metadata: Record<string, unknown>;
}

export interface GraphMetadata {
  sourceRef: string;
  sourceKind: string;
  nodeCount: number;
  edgeCount: number;
  /** Hub/worker supplied revision. Legacy payloads deliberately leave this empty. */
  graphRevision?: string;
  metricCapabilities?: readonly GraphMetricCapability[];
  [key: string]: unknown;
}

/**
 * Typed projection-window evidence mapped at the API boundary.
 *
 * Null means that the producer did not provide valid structured evidence for
 * the field. It must never be interpreted as zero or as proof of completeness.
 */
export interface GraphWindowEvidence {
  readonly view: string | null;
  readonly nextCursor: string | null;
  readonly totalNodes: number | null;
  readonly totalEdges: number | null;
  readonly sourceEdges: number | null;
  readonly unresolvedEdges: number | null;
  readonly internalEdges: number | null;
  readonly edgeCapped: boolean | null;
  readonly maxEdges: number | null;
}

/** Structured worker budget evidence. Unknown or invalid values remain null. */
export interface GraphSemanticBudgetEvidence {
  readonly configuredMaxRecordsPerPartition: number | null;
  readonly maxRecordsPerPartition: number | null;
  readonly maxBytesPerPartition: number | null;
  readonly configurationClamped: boolean | null;
  readonly truncated: boolean | null;
  readonly truncatedNodeCount: number | null;
  readonly truncatedEdgeCount: number | null;
  readonly unresolvedEdgeCount: number | null;
  readonly semanticNodeBytes: number | null;
  readonly semanticEdgeBytes: number | null;
  readonly candidateEdgeRecordLimit: number | null;
  readonly candidateEdgeByteLimit: number | null;
  readonly candidateEdgeCount: number | null;
  readonly candidateEdgeBytes: number | null;
  readonly truncatedCandidateEdgeCount: number | null;
}

export interface GraphSemanticTranslationEvidence {
  readonly schema: string | null;
  readonly status: string | null;
  readonly reasonCode: string | null;
  readonly semanticNodeCount: number | null;
  readonly semanticEdgeCount: number | null;
  readonly equivalenceRuleCount: number | null;
  readonly translationContractCount: number | null;
  readonly transformArtifactCount: number | null;
  readonly budget: Readonly<GraphSemanticBudgetEvidence> | null;
}

export interface GraphArtifactStatusEvidence {
  readonly state: string | null;
  readonly reasonCode: string | null;
  readonly knowledgeIndexId: string | null;
  readonly manifestPresent: boolean | null;
}

/**
 * Optional evidence keeps manually constructed and legacy graph models source
 * compatible. Consumers must treat an absent section as unknown.
 */
export interface GraphEvidence {
  readonly window: Readonly<GraphWindowEvidence> | null;
  readonly semanticTranslation: Readonly<GraphSemanticTranslationEvidence> | null;
  readonly artifactStatus: Readonly<GraphArtifactStatusEvidence> | null;
}

export interface GenericGraphModel {
  nodes: GraphNode[];
  edges: GraphEdge[];
  metadata: GraphMetadata;
  warnings: string[];
  evidence?: Readonly<GraphEvidence>;
}
