import { Injectable } from '@angular/core';
import {
  GraphArtifactStatusEvidence,
  GraphEvidence,
  GenericGraphModel,
  GraphEdge,
  GraphEdgeType,
  GraphNode,
  GraphNodeKind,
  GraphSemanticBudgetEvidence,
  GraphSemanticTranslationEvidence,
  GraphWindowEvidence,
  SEMANTIC_TRANSLATION_EDGE_TYPES,
  SEMANTIC_TRANSLATION_NODE_KINDS,
} from '../models/graph.model';
import {
  GraphMetricCapability,
  GraphMetricDatum,
  GraphMetricEntity,
  GraphMetricVector,
  MetricAvailability,
} from '../models/graph-visual-metrics.model';

// Raw domain_graph_artifact.v1 shapes returned by GET /api/codecompass/graph.
interface RawNode {
  node_id: string;
  node_type: string;
  raw_node_type?: string;
  known_kind?: string | boolean | null;
  metrics?: Record<string, unknown>;
  attributes?: Record<string, unknown>;
}

interface RawEdge {
  edge_id?: string;
  source_id: string;
  target_id: string;
  relation: string;
  raw_edge_type?: string;
  known_relation?: string | boolean | null;
  multiplicity?: number;
  directed?: boolean;
  metrics?: Record<string, unknown>;
  attributes?: Record<string, unknown>;
}

interface RawDomainGraphArtifact {
  schema?: string;
  source_kind?: string;
  source_ref?: string;
  graph_revision?: string;
  metric_capabilities?: unknown;
  nodes?: RawNode[];
  edges?: RawEdge[];
  metadata?: Record<string, unknown>;
  diagnostics?: unknown;
  artifact_status?: unknown;
  warnings?: string[];
}

const WINDOW_EVIDENCE_KEYS = Object.freeze([
  'view',
  'next_cursor',
  'total_nodes',
  'total_edges',
  'source_edge_count',
  'unresolved_edge_count',
  'internal_edge_count',
  'edge_capped',
  'max_edges',
] as const);

const SEMANTIC_BUDGET_KEYS = Object.freeze([
  'configured_max_records_per_partition',
  'max_records_per_partition',
  'max_bytes_per_partition',
  'configuration_clamped',
  'truncated',
  'truncated_node_count',
  'truncated_edge_count',
  'unresolved_edge_count',
  'semantic_node_bytes',
  'semantic_edge_bytes',
  'candidate_edge_record_limit',
  'candidate_edge_byte_limit',
  'candidate_edge_count',
  'candidate_edge_bytes',
  'truncated_candidate_edge_count',
] as const);

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
  'package_manager', 'external_package', 'buildable_component',
  'repository', 'directory', 'source_file',
  'aggregator', 'runner', 'test',
  ...SEMANTIC_TRANSLATION_NODE_KINDS,
]);

const NODE_KIND_ALIASES: Readonly<Record<string, string>> = Object.freeze({
  ts_file: 'typescript_file',
});

const KNOWN_EDGE_TYPES = new Set<string>([
  'bean_factory_method',
  'calls_probable_target',
  'child_of_type',
  'child_of_file',
  'contains_directory',
  'contains_entry',
  'contains_file',
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
  'frontend_guard_refs_field',
  'generic_type_uses',
  'implements',
  'imports_module',
  'imports_symbol',
  'injects_dependency',
  'transactional_boundary',
  'jpa_relation',
  'mapper_maps_type',
  'method_param_type_uses',
  'method_return_type_uses',
  'parent_child',
  'permission_checks_field',
  'policy_applies_to_field',
  'returns',
  'uses_type',
  'related',
  'wiki_link',
  'service_uses_repository',
  'test_calls_endpoint',
  'test_targets_type',
  'depends_on', 'aggregates', 'built_by', 'tested_by', 'runs', 'covers',
  ...SEMANTIC_TRANSLATION_EDGE_TYPES,
]);

const METRIC_ID_ALIASES: Readonly<Record<string, string>> = Object.freeze({
  degree: 'total_degree',
  code_size: 'code_extent',
  usage: 'usage_frequency',
});

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function optionalRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function hasOwn(record: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function hasAnyOwn(record: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.some(key => hasOwn(record, key));
}

function evidenceText(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.trim();
  return normalized || null;
}

function evidenceBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function evidenceCount(value: unknown): number | null {
  return typeof value === 'number'
    && Number.isFinite(value)
    && Number.isInteger(value)
    && value >= 0
    ? value
    : null;
}

function metricId(raw: string): string {
  return METRIC_ID_ALIASES[raw] ?? raw;
}

function availability(value: unknown): MetricAvailability {
  return value === 'available' || value === 'approximate' || value === 'not_applicable'
    ? value
    : 'unavailable';
}

@Injectable({ providedIn: 'root' })
export class GraphAdapterService {
  fromDomainArtifact(raw: unknown): GenericGraphModel {
    const artifact = (raw ?? {}) as RawDomainGraphArtifact;
    const rawNodes = Array.isArray(artifact.nodes) ? artifact.nodes : [];
    const rawEdges = Array.isArray(artifact.edges) ? artifact.edges : [];
    const meta = asRecord(artifact.metadata);
    const capabilities = this.withIntrinsicEdgeCapabilities(
      this.mapCapabilities(artifact.metric_capabilities ?? meta['metric_capabilities']),
      rawEdges,
    );
    const capabilityIndex = new Map(capabilities.map(capability => [
      `${capability.entity}:${capability.metricId}`,
      capability,
    ]));

    const nodes = rawNodes
      .filter(node => node && typeof node.node_id === 'string' && node.node_id)
      .map((node): GraphNode => this.mapNode(node, capabilityIndex));

    const edgeOccurrences = new Map<string, number>();
    const edges = rawEdges
      .filter(edge => edge && edge.source_id && edge.target_id)
      .map((edge): GraphEdge => this.mapEdge(edge, capabilityIndex, edgeOccurrences));

    const graphRevision = String(artifact.graph_revision ?? meta['graph_revision'] ?? '').trim();
    const metadata = {
      sourceRef: String(artifact.source_ref ?? ''),
      sourceKind: String(artifact.source_kind ?? ''),
      nodeCount: nodes.length,
      edgeCount: edges.length,
      ...meta,
    } as GenericGraphModel['metadata'];
    if (graphRevision) metadata.graphRevision = graphRevision;
    if (capabilities.length > 0) metadata.metricCapabilities = capabilities;
    const evidence = this.mapEvidence(artifact, meta);

    return {
      nodes,
      edges,
      metadata,
      warnings: Array.isArray(artifact.warnings)
        ? artifact.warnings.filter((warning): warning is string => typeof warning === 'string')
        : [],
      ...(evidence ? { evidence } : {}),
    };
  }

  private mapEvidence(
    artifact: RawDomainGraphArtifact,
    metadata: Record<string, unknown>,
  ): Readonly<GraphEvidence> | undefined {
    const window = this.mapWindowEvidence(metadata);
    const diagnostics = optionalRecord(artifact.diagnostics);
    const semanticDiagnostics = diagnostics
      ? optionalRecord(diagnostics['semantic_translation'])
      : null;
    const metadataBudget = optionalRecord(metadata['semantic_budget']);
    const diagnosticBudget = semanticDiagnostics
      ? optionalRecord(semanticDiagnostics['semantic_budget'])
      : null;
    const semanticTranslation = this.mapSemanticTranslationEvidence(
      semanticDiagnostics,
      metadataBudget,
      diagnosticBudget,
    );
    const artifactStatus = this.mapArtifactStatusEvidence(artifact.artifact_status);
    if (!window && !semanticTranslation && !artifactStatus) return undefined;
    return Object.freeze({ window, semanticTranslation, artifactStatus });
  }

  private mapWindowEvidence(
    metadata: Record<string, unknown>,
  ): Readonly<GraphWindowEvidence> | null {
    if (!hasAnyOwn(metadata, WINDOW_EVIDENCE_KEYS)) return null;
    return Object.freeze({
      view: evidenceText(metadata['view']),
      nextCursor: evidenceText(metadata['next_cursor']),
      totalNodes: evidenceCount(metadata['total_nodes']),
      totalEdges: evidenceCount(metadata['total_edges']),
      sourceEdges: evidenceCount(metadata['source_edge_count']),
      unresolvedEdges: evidenceCount(metadata['unresolved_edge_count']),
      internalEdges: evidenceCount(metadata['internal_edge_count']),
      edgeCapped: evidenceBoolean(metadata['edge_capped']),
      maxEdges: evidenceCount(metadata['max_edges']),
    });
  }

  private mapSemanticTranslationEvidence(
    diagnostics: Record<string, unknown> | null,
    metadataBudget: Record<string, unknown> | null,
    diagnosticBudget: Record<string, unknown> | null,
  ): Readonly<GraphSemanticTranslationEvidence> | null {
    if (!diagnostics && !metadataBudget && !diagnosticBudget) return null;
    const combinedBudget = metadataBudget || diagnosticBudget
      ? { ...(diagnosticBudget ?? {}), ...(metadataBudget ?? {}) }
      : null;
    const budget = combinedBudget
      ? this.mapSemanticBudgetEvidence(combinedBudget)
      : null;
    const values = diagnostics ?? {};
    return Object.freeze({
      schema: evidenceText(values['schema']),
      status: evidenceText(values['status']),
      reasonCode: evidenceText(values['reason']),
      semanticNodeCount: evidenceCount(values['semantic_node_count']),
      semanticEdgeCount: evidenceCount(values['semantic_edge_count']),
      equivalenceRuleCount: evidenceCount(values['equivalence_rule_count']),
      translationContractCount: evidenceCount(values['translation_contract_count']),
      transformArtifactCount: evidenceCount(values['transform_artifact_count']),
      budget,
    });
  }

  private mapSemanticBudgetEvidence(
    budget: Record<string, unknown>,
  ): Readonly<GraphSemanticBudgetEvidence> | null {
    if (!hasAnyOwn(budget, SEMANTIC_BUDGET_KEYS)) return null;
    return Object.freeze({
      configuredMaxRecordsPerPartition: evidenceCount(
        budget['configured_max_records_per_partition'],
      ),
      maxRecordsPerPartition: evidenceCount(budget['max_records_per_partition']),
      maxBytesPerPartition: evidenceCount(budget['max_bytes_per_partition']),
      configurationClamped: evidenceBoolean(budget['configuration_clamped']),
      truncated: evidenceBoolean(budget['truncated']),
      truncatedNodeCount: evidenceCount(budget['truncated_node_count']),
      truncatedEdgeCount: evidenceCount(budget['truncated_edge_count']),
      unresolvedEdgeCount: evidenceCount(budget['unresolved_edge_count']),
      semanticNodeBytes: evidenceCount(budget['semantic_node_bytes']),
      semanticEdgeBytes: evidenceCount(budget['semantic_edge_bytes']),
      candidateEdgeRecordLimit: evidenceCount(budget['candidate_edge_record_limit']),
      candidateEdgeByteLimit: evidenceCount(budget['candidate_edge_byte_limit']),
      candidateEdgeCount: evidenceCount(budget['candidate_edge_count']),
      candidateEdgeBytes: evidenceCount(budget['candidate_edge_bytes']),
      truncatedCandidateEdgeCount: evidenceCount(
        budget['truncated_candidate_edge_count'],
      ),
    });
  }

  private mapArtifactStatusEvidence(
    raw: unknown,
  ): Readonly<GraphArtifactStatusEvidence> | null {
    const stringState = evidenceText(raw);
    if (stringState) {
      return Object.freeze({
        state: stringState,
        reasonCode: null,
        knowledgeIndexId: null,
        manifestPresent: null,
      });
    }
    const status = optionalRecord(raw);
    if (!status) return null;
    return Object.freeze({
      state: evidenceText(status['state']) ?? evidenceText(status['status']),
      reasonCode: evidenceText(status['reason_code']),
      knowledgeIndexId: evidenceText(status['knowledge_index_id']),
      manifestPresent: evidenceBoolean(status['manifest_present']),
    });
  }

  private mapNode(
    raw: RawNode,
    capabilities: ReadonlyMap<string, GraphMetricCapability>,
  ): GraphNode {
    const attrs = asRecord(raw.attributes);
    const rawNodeType = String(raw.raw_node_type ?? attrs['raw_node_type'] ?? raw.node_type ?? 'unknown');
    const declaredKind = raw.known_kind ?? attrs['known_kind'];
    const semanticUnknown = attrs['semantic_status'] === 'semantically_unknown';
    const knownKindValue = semanticUnknown
      ? ''
      : typeof declaredKind === 'string'
      ? declaredKind
      : declaredKind === false || declaredKind === null
        ? ''
        : rawNodeType;
    const normalizedKindValue = knownKindValue.trim().toLowerCase();
    const canonicalKindValue = NODE_KIND_ALIASES[normalizedKindValue] ?? normalizedKindValue;
    const knownKind = KNOWN_NODE_KINDS.has(canonicalKindValue)
      ? canonicalKindValue as GraphNodeKind
      : null;
    const kind = knownKind ?? 'unknown';
    const { file, name, content, record_id, metrics, visual_metrics, ...rest } = attrs;
    const fileStr = String(file ?? '');
    const fallbackLabel = fileStr
      ? fileStr.split('/').pop() ?? raw.node_id
      : raw.node_id;
    const metricVector = this.mapMetricVector(
      raw.metrics ?? asRecord(metrics ?? visual_metrics),
      'node',
      capabilities,
    );
    return {
      id: raw.node_id,
      kind,
      rawNodeType,
      knownKind,
      label: String(name || '') || fallbackLabel,
      file: fileStr,
      content: String(content ?? ''),
      recordId: String(record_id ?? ''),
      domainId: this.optionalString(attrs['domain_id']),
      domainPath: this.optionalString(attrs['domain_path']),
      metrics: metricVector,
      metadata: rest,
    };
  }

  private mapEdge(
    raw: RawEdge,
    capabilities: ReadonlyMap<string, GraphMetricCapability>,
    occurrences: Map<string, number>,
  ): GraphEdge {
    const attrs = asRecord(raw.attributes);
    const rawEdgeType = String(raw.raw_edge_type ?? attrs['raw_edge_type'] ?? raw.relation ?? 'related');
    const declaredRelation = raw.known_relation ?? attrs['known_relation'];
    const semanticUnknown = attrs['semantic_status'] === 'semantically_unknown';
    const knownRelationValue = semanticUnknown
      ? ''
      : typeof declaredRelation === 'string'
      ? declaredRelation
      : declaredRelation === false || declaredRelation === null
        ? ''
        : rawEdgeType;
    const canonicalRelationValue = knownRelationValue.trim().toLowerCase();
    const knownRelation = KNOWN_EDGE_TYPES.has(canonicalRelationValue)
      ? canonicalRelationValue as GraphEdgeType
      : null;
    const edgeType = knownRelation ?? 'related';
    const confidenceValue = raw.metrics?.['confidence'] ?? attrs['confidence'];
    const confidence = typeof confidenceValue === 'number'
      && Number.isFinite(confidenceValue)
      && confidenceValue >= 0
      && confidenceValue <= 1
      ? confidenceValue
      : 1;
    const multiplicityValue = raw.multiplicity ?? attrs['multiplicity'];
    const multiplicity = typeof multiplicityValue === 'number'
      && Number.isFinite(multiplicityValue)
      && multiplicityValue >= 0
      ? multiplicityValue
      : 1;
    const directedValue = raw.directed ?? attrs['directed'];
    const directed = typeof directedValue === 'boolean' ? directedValue : true;
    const explicitId = String(raw.edge_id ?? attrs['edge_id'] ?? '').trim();
    const baseId = explicitId || `${raw.source_id}|${raw.target_id}|${rawEdgeType}`;
    const occurrence = occurrences.get(baseId) ?? 0;
    occurrences.set(baseId, occurrence + 1);
    const edgeId = occurrence === 0 ? baseId : `${baseId}|parallel:${occurrence + 1}`;

    const rawMetrics = {
      ...asRecord(attrs['metrics'] ?? attrs['visual_metrics']),
      ...asRecord(raw.metrics),
    };
    if (!Object.prototype.hasOwnProperty.call(rawMetrics, 'confidence')) rawMetrics['confidence'] = confidence;
    if (!Object.prototype.hasOwnProperty.call(rawMetrics, 'multiplicity')) rawMetrics['multiplicity'] = multiplicity;
    if (!Object.prototype.hasOwnProperty.call(rawMetrics, 'dependency_weight')
      && typeof attrs['dependency_weight'] === 'number') {
      rawMetrics['dependency_weight'] = attrs['dependency_weight'];
    }
    const metricVector = this.mapMetricVector(rawMetrics, 'edge', capabilities);
    const {
      confidence: _confidence,
      multiplicity: _multiplicity,
      directed: _directed,
      edge_id: _edgeId,
      metrics: _metrics,
      visual_metrics: _visualMetrics,
      ...rest
    } = attrs;
    return {
      id: edgeId,
      source: raw.source_id,
      target: raw.target_id,
      edgeType,
      rawEdgeType,
      knownRelation,
      confidence,
      multiplicity,
      directed,
      selfLoop: raw.source_id === raw.target_id,
      metrics: metricVector,
      metadata: rest,
    };
  }

  private mapMetricVector(
    raw: unknown,
    entity: GraphMetricEntity,
    capabilities: ReadonlyMap<string, GraphMetricCapability>,
  ): GraphMetricVector {
    const vector: Record<string, GraphMetricDatum> = {};
    for (const [rawId, rawDatum] of Object.entries(asRecord(raw)).sort(([left], [right]) => left.localeCompare(right))) {
      const canonicalId = metricId(rawId);
      const capability = capabilities.get(`${entity}:${canonicalId}`)
        ?? capabilities.get(`graph:${canonicalId}`);
      if (typeof rawDatum === 'number') {
        vector[canonicalId] = {
          value: rawDatum,
          availability: capability?.availability ?? 'available',
          provenance: capability ? {
            source: capability.source,
            algorithmVersion: capability.algorithmVersion,
            graphRevision: capability.graphRevision,
          } : undefined,
          reasonCode: capability?.reasonCode,
        };
        continue;
      }
      const record = asRecord(rawDatum);
      const value = record['value'];
      const status = availability(record['availability'] ?? record['status'] ?? capability?.availability ?? 'unavailable');
      vector[canonicalId] = {
        value: typeof value === 'number' ? value : undefined,
        availability: status,
        provenance: {
          source: String(record['source'] ?? capability?.source ?? 'graph_payload'),
          algorithmVersion: String(
            record['algorithmVersion']
              ?? record['algorithm_version']
              ?? capability?.algorithmVersion
              ?? 'unknown',
          ),
          graphRevision: this.optionalString(
            record['graphRevision']
              ?? record['graph_revision']
              ?? capability?.graphRevision,
          ),
        },
        reasonCode: this.optionalString(record['reasonCode'] ?? record['reason_code'] ?? capability?.reasonCode),
      };
    }
    return Object.freeze(vector);
  }

  private mapCapabilities(raw: unknown): readonly GraphMetricCapability[] {
    const result: GraphMetricCapability[] = [];
    if (Array.isArray(raw)) {
      for (const entry of raw) {
        const record = asRecord(entry);
        const id = String(record['metricId'] ?? record['metric_id'] ?? record['id'] ?? '').trim();
        if (!id) continue;
        result.push(this.capability(metricId(id), record));
      }
    } else {
      for (const [id, entry] of Object.entries(asRecord(raw))) {
        result.push(this.capability(metricId(id), asRecord(entry)));
      }
    }
    return Object.freeze(result.sort((left, right) =>
      left.entity.localeCompare(right.entity) || left.metricId.localeCompare(right.metricId)));
  }

  private withIntrinsicEdgeCapabilities(
    capabilities: readonly GraphMetricCapability[],
    edges: readonly RawEdge[],
  ): readonly GraphMetricCapability[] {
    const result = [...capabilities];
    const existing = new Set(result.map(capability => `${capability.entity}:${capability.metricId}`));
    const base = {
      entity: 'edge' as const,
      source: 'domain_graph_artifact.v1',
      algorithmVersion: 'graph-adapter.intrinsic.v1',
    };
    const empty = edges.length === 0;
    const intrinsicEvidenceCount = (
      metric: 'confidence' | 'multiplicity',
      maximum?: number,
    ): number => edges.filter(edge => {
      const attrs = asRecord(edge.attributes);
      const metrics = { ...asRecord(attrs['metrics'] ?? attrs['visual_metrics']), ...asRecord(edge.metrics) };
      const direct = metric === 'multiplicity' ? edge.multiplicity : undefined;
      const value = direct ?? metrics[metric] ?? attrs[metric];
      return typeof value === 'number'
        && Number.isFinite(value)
        && value >= 0
        && (maximum === undefined || value <= maximum);
    }).length;
    const intrinsicCapability = (
      metricIdValue: 'confidence' | 'multiplicity',
      evidenceCount: number,
    ): GraphMetricCapability => ({
      ...base,
      metricId: metricIdValue,
      availability: empty
        ? 'not_applicable'
        : evidenceCount === edges.length
          ? 'available'
          : 'approximate',
      reasonCode: empty
        ? 'empty_graph'
        : evidenceCount === edges.length
          ? undefined
          : evidenceCount > 0
            ? 'partial_edge_evidence'
            : `${metricIdValue}_defaulted`,
      scope: !empty && evidenceCount > 0 && evidenceCount < edges.length ? 'subset' : 'all_edges',
      limits: !empty && evidenceCount !== edges.length
        ? Object.freeze({ evidence_edge_count: evidenceCount, graph_edge_count: edges.length })
        : undefined,
    });
    const fallbacks: GraphMetricCapability[] = [
      intrinsicCapability('confidence', intrinsicEvidenceCount('confidence', 1)),
      intrinsicCapability('multiplicity', intrinsicEvidenceCount('multiplicity')),
    ];
    const dependencyEvidence = edges.filter(edge => {
      const attrs = asRecord(edge.attributes);
      const metrics = { ...asRecord(attrs['metrics'] ?? attrs['visual_metrics']), ...asRecord(edge.metrics) };
      const value = metrics['dependency_weight'] ?? attrs['dependency_weight'];
      return typeof value === 'number' && Number.isFinite(value) && value >= 0;
    }).length;
    fallbacks.push({
      ...base,
      metricId: 'dependency_weight',
      availability: empty
        ? 'not_applicable'
        : dependencyEvidence === edges.length
          ? 'available'
          : dependencyEvidence > 0
            ? 'approximate'
            : 'unavailable',
      reasonCode: empty
        ? 'empty_graph'
        : dependencyEvidence === edges.length
          ? undefined
          : dependencyEvidence > 0
            ? 'partial_edge_evidence'
            : 'dependency_weight_evidence_missing',
    });
    for (const capability of fallbacks) {
      if (!existing.has(`${capability.entity}:${capability.metricId}`)) result.push(capability);
    }
    return Object.freeze(result.sort((left, right) =>
      left.entity.localeCompare(right.entity) || left.metricId.localeCompare(right.metricId)));
  }

  private capability(metricIdValue: string, raw: Record<string, unknown>): GraphMetricCapability {
    const scope = String(raw['entity'] ?? raw['scope'] ?? 'node');
    const entity: GraphMetricEntity = scope === 'edge' || scope === 'all_edges'
      ? 'edge'
      : scope === 'graph'
        ? 'graph'
        : 'node';
    return Object.freeze({
      metricId: metricIdValue,
      entity,
      availability: availability(raw['availability'] ?? raw['status']),
      source: String(raw['source'] ?? 'unknown'),
      algorithmVersion: String(raw['algorithmVersion'] ?? raw['algorithm_version'] ?? 'unknown'),
      reasonCode: this.optionalString(raw['reasonCode'] ?? raw['reason_code']),
      scope: this.optionalString(raw['scope']),
      graphRevision: this.optionalString(raw['graphRevision'] ?? raw['graph_revision']),
      limits: Object.freeze({ ...asRecord(raw['limits']) }) as Readonly<Record<string, string | number | boolean | null>>,
    });
  }

  private optionalString(value: unknown): string | undefined {
    if (value === null || value === undefined) return undefined;
    const result = String(value).trim();
    return result || undefined;
  }
}
