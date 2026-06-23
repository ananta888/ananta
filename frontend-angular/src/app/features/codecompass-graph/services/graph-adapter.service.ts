import { Injectable } from '@angular/core';
import { GenericGraphModel, GraphEdge, GraphEdgeType, GraphNode, GraphNodeKind } from '../models/graph.model';

// Raw domain_graph_artifact.v1 shapes returned by GET /api/codecompass/graph
interface RawNode {
  node_id: string;
  node_type: string;
  attributes?: Record<string, unknown>;
}

interface RawEdge {
  source_id: string;
  target_id: string;
  relation: string;
  attributes?: Record<string, unknown>;
}

interface RawDomainGraphArtifact {
  schema?: string;
  source_kind?: string;
  source_ref?: string;
  nodes?: RawNode[];
  edges?: RawEdge[];
  metadata?: Record<string, unknown>;
  warnings?: string[];
}

const KNOWN_NODE_KINDS = new Set<string>([
  'java_constructor', 'java_constructor_detail', 'java_file', 'java_method',
  'java_method_detail', 'java_module_summary', 'java_type',
  'md_file', 'md_section',
  'properties_entry', 'properties_file',
  'python_class', 'python_file', 'python_function', 'python_import',
  'python_method', 'python_module_summary',
  'typescript_class', 'typescript_const', 'typescript_constructor',
  'typescript_enum', 'typescript_file', 'typescript_folder_summary',
  'typescript_function', 'typescript_import', 'typescript_interface',
  'typescript_method', 'typescript_type',
  'xml_file', 'xml_node_detail', 'xml_tag',
  'yaml_entry', 'yaml_file',
  'config',
  'wiki_article', 'wiki_section', 'wiki_chunk',
  'unknown',
]);

const KNOWN_EDGE_TYPES = new Set<string>([
  'bean_factory_method',
  'calls_probable_target',
  'child_of_type',
  'child_of_file',
  'contains_entry',
  'contains_method',
  'contains_section',
  'contains_symbol',
  'contains_type',
  'controller_endpoint_declares',
  'declares_constructor',
  'declares_method',
  'declares_bean',
  'extends',
  'field_type_uses',
  'generic_type_uses',
  'implements',
  'imports_module',
  'imports_symbol',
  'injects_dependency',
  'transactional_boundary',
  'jpa_relation',
  'method_param_type_uses',
  'method_return_type_uses',
  'parent_child',
  'returns',
  'uses_type',
  'related',
]);

@Injectable({ providedIn: 'root' })
export class GraphAdapterService {

  fromDomainArtifact(raw: unknown): GenericGraphModel {
    const artifact = (raw ?? {}) as RawDomainGraphArtifact;
    const rawNodes = Array.isArray(artifact.nodes) ? artifact.nodes : [];
    const rawEdges = Array.isArray(artifact.edges) ? artifact.edges : [];
    const meta = (artifact.metadata ?? {}) as Record<string, unknown>;

    const nodes = rawNodes
      .filter(n => n && typeof n.node_id === 'string' && n.node_id)
      .map((n): GraphNode => this._mapNode(n));

    const edges = rawEdges
      .filter(e => e && e.source_id && e.target_id)
      .map((e): GraphEdge => this._mapEdge(e));

    return {
      nodes,
      edges,
      metadata: {
        sourceRef: String(artifact.source_ref ?? ''),
        sourceKind: String(artifact.source_kind ?? ''),
        nodeCount: nodes.length,
        edgeCount: edges.length,
        ...meta,
      },
      warnings: Array.isArray(artifact.warnings)
        ? artifact.warnings.filter((w): w is string => typeof w === 'string')
        : [],
    };
  }

  private _mapNode(raw: RawNode): GraphNode {
    const attrs = (raw.attributes ?? {}) as Record<string, unknown>;
    const kind = KNOWN_NODE_KINDS.has(String(raw.node_type))
      ? (raw.node_type as GraphNodeKind)
      : 'unknown';
    const { file, name, content, record_id, ...rest } = attrs;
    const fileStr = String(file ?? '');
    const fallbackLabel = fileStr
      ? fileStr.split('/').pop() ?? raw.node_id
      : raw.node_id;
    return {
      id: raw.node_id,
      kind,
      label: String(name || '') || fallbackLabel,
      file: fileStr,
      content: String(content ?? ''),
      recordId: String(record_id ?? ''),
      metadata: rest,
    };
  }

  private _mapEdge(raw: RawEdge): GraphEdge {
    const attrs = (raw.attributes ?? {}) as Record<string, unknown>;
    const edgeType = KNOWN_EDGE_TYPES.has(String(raw.relation))
      ? (raw.relation as GraphEdgeType)
      : 'related';
    const { confidence, ...rest } = attrs;
    return {
      id: `${raw.source_id}|${raw.target_id}|${raw.relation}`,
      source: raw.source_id,
      target: raw.target_id,
      edgeType,
      confidence: typeof confidence === 'number' ? confidence : 1.0,
      metadata: rest,
    };
  }
}
