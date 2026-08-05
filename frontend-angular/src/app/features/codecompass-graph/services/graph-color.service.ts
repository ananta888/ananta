import { Injectable } from '@angular/core';
import { graphEdgeColor } from '../models/graph-edge-style';
import {
  GraphDomainIdentity,
  GraphEdge,
  GraphNode,
  GraphNodeKind,
} from '../models/graph.model';
import { GraphVisualProfile } from '../models/graph-visual-profile.model';
import { GraphVisualMarker } from '../models/graph-visual-metrics.model';

export interface GraphNodeVisualIdentity {
  domain: Readonly<GraphDomainIdentity>;
  color: string;
  marker: GraphVisualMarker;
  label: string;
}

export interface GraphEdgeVisualIdentity {
  rawEdgeType: string;
  color: string;
  marker: GraphVisualMarker;
  label: string;
  semanticallyKnown: boolean;
}

export const GRAPH_COLOR_ALGORITHM_VERSION = 'graph-color.v1';

const UNASSIGNED_DOMAIN = 'unassigned';
const NEUTRAL_NODE_COLOR = '#64748B';
const MARKERS: readonly GraphVisualMarker[] = Object.freeze([
  'circle', 'square', 'triangle', 'diamond', 'hexagon', 'ring', 'cross', 'star',
]);

const NODE_KIND_COLORS: Readonly<Partial<Record<GraphNodeKind, string>>> = Object.freeze({
  python_class: '#2563EB',
  python_function: '#0EA5E9',
  python_method: '#0284C7',
  python_file: '#0369A1',
  typescript_class: '#7C3AED',
  typescript_function: '#8B5CF6',
  typescript_method: '#9333EA',
  typescript_file: '#6D28D9',
  java_type: '#EA580C',
  java_method: '#F97316',
  java_file: '#C2410C',
  md_file: '#059669',
  md_section: '#10B981',
  wiki_article: '#0D9488',
  wiki_section: '#14B8A6',
  wiki_chunk: '#2DD4BF',
  package_manager: '#A16207',
  external_package: '#CA8A04',
  buildable_component: '#BE123C',
  repository: '#0F766E',
  directory: '#0D9488',
  source_file: '#14B8A6',
  syntax_node: '#2563EB',
  semantic_node: '#7C3AED',
  type_node: '#9333EA',
  symbol_node: '#0891B2',
  control_flow_node: '#EA580C',
  data_flow_node: '#0D9488',
  effect_node: '#DC2626',
  contract_node: '#BE123C',
  equivalence_rule: '#CA8A04',
  transform_artifact: '#4F46E5',
  aggregator: '#9F1239',
  runner: '#4F46E5',
  test: '#16A34A',
  config: '#475569',
  unknown: NEUTRAL_NODE_COLOR,
});

function stableHash(value: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  hash ^= hash >>> 16;
  hash = Math.imul(hash, 0x7feb352d) >>> 0;
  hash ^= hash >>> 15;
  return Math.imul(hash, 0x846ca68b) >>> 0;
}

function hslToHex(hue: number, saturation: number, lightness: number): string {
  const saturationFraction = saturation / 100;
  const lightnessFraction = lightness / 100;
  const chroma = (1 - Math.abs(2 * lightnessFraction - 1)) * saturationFraction;
  const section = hue / 60;
  const intermediate = chroma * (1 - Math.abs((section % 2) - 1));
  const [redPart, greenPart, bluePart] = section < 1
    ? [chroma, intermediate, 0]
    : section < 2
      ? [intermediate, chroma, 0]
      : section < 3
        ? [0, chroma, intermediate]
        : section < 4
          ? [0, intermediate, chroma]
          : section < 5
            ? [intermediate, 0, chroma]
            : [chroma, 0, intermediate];
  const offset = lightnessFraction - chroma / 2;
  return `#${[redPart, greenPart, bluePart]
    .map(channel => Math.round((channel + offset) * 255).toString(16).padStart(2, '0'))
    .join('')}`.toUpperCase();
}

function labelFromIdentifier(value: string): string {
  return value
    .replace(/^file:/, '')
    .split(/[./\\]/)
    .filter(Boolean)
    .pop()
    ?.replace(/[_-]+/g, ' ') || value;
}

@Injectable({ providedIn: 'root' })
export class GraphColorService {
  resolveCanonicalDomain(node: GraphNode): Readonly<GraphDomainIdentity> {
    const explicitDomainId = this.nonEmpty(node.domainId ?? node.metadata['domain_id']);
    if (explicitDomainId) {
      return Object.freeze({
        canonicalId: explicitDomainId,
        label: this.nonEmpty(node.metadata['domain_label']) ?? labelFromIdentifier(explicitDomainId),
        source: 'domain_id',
      });
    }
    const domainPath = this.nonEmpty(node.domainPath ?? node.metadata['domain_path']);
    if (domainPath) {
      return Object.freeze({
        canonicalId: domainPath,
        label: this.nonEmpty(node.metadata['domain_label']) ?? labelFromIdentifier(domainPath),
        source: 'domain_path',
      });
    }
    const normalizedFile = node.file.trim().replace(/\\/g, '/').replace(/^\.\//, '');
    if (normalizedFile) {
      return Object.freeze({
        canonicalId: `file:${normalizedFile}`,
        label: labelFromIdentifier(normalizedFile),
        source: 'file',
      });
    }
    return Object.freeze({ canonicalId: UNASSIGNED_DOMAIN, label: 'Nicht zugeordnet', source: 'unassigned' });
  }

  nodeVisual(node: GraphNode, profile: GraphVisualProfile): Readonly<GraphNodeVisualIdentity> {
    const domain = this.resolveCanonicalDomain(node);
    if (domain.source !== 'unassigned') {
      return Object.freeze({
        domain,
        color: profile.domainColorOverrides[domain.canonicalId] ?? this.automaticDomainColor(domain.canonicalId),
        marker: this.marker(domain.canonicalId),
        label: domain.label,
      });
    }
    const rawKind = node.rawNodeType ?? node.kind;
    const knownKind = node.knownKind ?? (node.kind === 'unknown' ? null : node.kind);
    return Object.freeze({
      domain,
      color: profile.nodeKindColorOverrides[rawKind]
        ?? (knownKind ? NODE_KIND_COLORS[knownKind] : undefined)
        ?? NEUTRAL_NODE_COLOR,
      marker: this.marker(`kind:${rawKind}`),
      label: labelFromIdentifier(rawKind),
    });
  }

  edgeVisual(edge: GraphEdge, profile: GraphVisualProfile): Readonly<GraphEdgeVisualIdentity> {
    const rawEdgeType = edge.rawEdgeType ?? edge.edgeType;
    const knownRelation = edge.knownRelation ?? (edge.edgeType === 'related' && rawEdgeType !== 'related'
      ? null
      : edge.edgeType);
    return Object.freeze({
      rawEdgeType,
      color: profile.relationColorOverrides[rawEdgeType]
        ?? profile.relationColorOverrides[edge.edgeType]
        ?? graphEdgeColor(knownRelation ?? rawEdgeType),
      marker: this.marker(`relation:${rawEdgeType}`),
      label: labelFromIdentifier(rawEdgeType),
      semanticallyKnown: knownRelation !== null,
    });
  }

  automaticDomainColor(canonicalDomainId: string): string {
    const hash = stableHash(`${GRAPH_COLOR_ALGORITHM_VERSION}\u0000${canonicalDomainId}`);
    const hue = (hash % 3_600) / 10;
    const saturation = 58 + ((hash >>> 12) % 23);
    const lightness = 40 + ((hash >>> 20) % 17);
    return hslToHex(hue, saturation, lightness);
  }

  marker(identity: string): GraphVisualMarker {
    return MARKERS[stableHash(identity) % MARKERS.length];
  }

  private nonEmpty(value: unknown): string | undefined {
    if (value === undefined || value === null) return undefined;
    const result = String(value).trim();
    return result || undefined;
  }
}
