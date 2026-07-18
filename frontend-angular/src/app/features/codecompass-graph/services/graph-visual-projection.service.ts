import { Inject, Injectable } from '@angular/core';
import { GenericGraphModel } from '../models/graph.model';
import {
  DomainLegendEntry,
  EdgeVisualStyle,
  GraphVisualProjection,
  NodeVisualStyle,
  RelationLegendEntry,
} from '../models/graph-visual-metrics.model';
import {
  GRAPH_VISUAL_PROFILE_CACHE_MAX_ENTRIES,
  GraphVisualProfile,
  graphVisualProfileHash,
  graphVisualProfileSemanticHash,
} from '../models/graph-visual-profile.model';
import { GraphColorService } from './graph-color.service';
import {
  GraphMetricNormalizationContext,
  GraphMetricScoreService,
} from './graph-metric-score.service';

export interface GraphVisualProjectionCacheStats {
  normalizationEntries: number;
  projectionEntries: number;
}

class BoundedLruCache<T> {
  private readonly entries = new Map<string, T>();

  constructor(private readonly capacity: number) {}

  get(key: string): T | undefined {
    const value = this.entries.get(key);
    if (value === undefined) return undefined;
    this.entries.delete(key);
    this.entries.set(key, value);
    return value;
  }

  set(key: string, value: T): void {
    this.entries.delete(key);
    this.entries.set(key, value);
    while (this.entries.size > this.capacity) {
      const oldestKey = this.entries.keys().next().value as string | undefined;
      if (oldestKey === undefined) break;
      this.entries.delete(oldestKey);
    }
  }

  deleteWhere(predicate: (key: string) => boolean): void {
    for (const key of [...this.entries.keys()]) {
      if (predicate(key)) this.entries.delete(key);
    }
  }

  clear(): void {
    this.entries.clear();
  }

  get size(): number {
    return this.entries.size;
  }
}

interface MutableDomainLegend extends DomainLegendEntry {}
interface MutableRelationLegend extends RelationLegendEntry {}

@Injectable({ providedIn: 'root' })
export class GraphVisualProjectionService {
  private readonly normalizationCache = new BoundedLruCache<Readonly<GraphMetricNormalizationContext>>(
    GRAPH_VISUAL_PROFILE_CACHE_MAX_ENTRIES,
  );
  private readonly projectionCache = new BoundedLruCache<Readonly<GraphVisualProjection>>(
    GRAPH_VISUAL_PROFILE_CACHE_MAX_ENTRIES,
  );
  private readonly semanticProjectionCache = new BoundedLruCache<Readonly<GraphVisualProjection>>(
    GRAPH_VISUAL_PROFILE_CACHE_MAX_ENTRIES,
  );
  private readonly currentRevisionBySource = new BoundedLruCache<string>(
    GRAPH_VISUAL_PROFILE_CACHE_MAX_ENTRIES,
  );

  constructor(
    @Inject(GraphMetricScoreService)
    private readonly scoreService: GraphMetricScoreService,
    @Inject(GraphColorService)
    private readonly colorService: GraphColorService,
  ) {}

  project(
    graph: GenericGraphModel,
    profile: GraphVisualProfile,
  ): Readonly<GraphVisualProjection> {
    const revision = graph.metadata.graphRevision ?? '';
    const profileHash = graphVisualProfileHash(profile);
    if (!revision) {
      return this.computeProjection(graph, profile, profileHash, this.scoreService.createContext(graph));
    }

    const sourceKey = `${graph.metadata.sourceKind}\u0000${graph.metadata.sourceRef}`;
    const metricsHash = String(graph.metadata['visual_metrics_content_hash'] ?? '');
    const projectionAlgorithm = String(graph.metadata['projection_algorithm_version'] ?? '');
    const cacheRevision = `${revision}\u0000${metricsHash}\u0000${projectionAlgorithm}`;
    const normalizationKey = `${sourceKey}\u0000${cacheRevision}`;
    this.invalidatePreviousRevision(sourceKey, cacheRevision);
    const projectionKey = `${normalizationKey}\u0000${profileHash}`;
    const cachedProjection = this.projectionCache.get(projectionKey);
    if (cachedProjection) return cachedProjection;

    const semanticKey = `${normalizationKey}\u0000${graphVisualProfileSemanticHash(profile)}`;
    const semanticProjection = this.semanticProjectionCache.get(semanticKey);
    if (semanticProjection) {
      const rebound = this.rebindPresentation(semanticProjection, profile, profileHash);
      this.projectionCache.set(projectionKey, rebound);
      return rebound;
    }

    let context = this.normalizationCache.get(normalizationKey);
    if (!context) {
      context = this.scoreService.createContext(graph);
      this.normalizationCache.set(normalizationKey, context);
    }
    const projection = this.computeProjection(graph, profile, profileHash, context);
    this.semanticProjectionCache.set(semanticKey, projection);
    this.projectionCache.set(projectionKey, projection);
    return projection;
  }

  withVisibility(
    projection: Readonly<GraphVisualProjection>,
    graph: GenericGraphModel,
    visibleNodeIds: ReadonlySet<string>,
    visibleEdgeIds: ReadonlySet<string>,
  ): Readonly<GraphVisualProjection> {
    const visibleDomains = new Map<string, number>();
    for (const node of graph.nodes) {
      if (!visibleNodeIds.has(node.id)) continue;
      const domainId = this.colorService.resolveCanonicalDomain(node).canonicalId;
      visibleDomains.set(domainId, (visibleDomains.get(domainId) ?? 0) + 1);
    }
    const visibleRelations = new Map<string, number>();
    for (const edge of graph.edges) {
      if (!visibleEdgeIds.has(edge.id)) continue;
      const relation = edge.rawEdgeType ?? edge.edgeType;
      visibleRelations.set(relation, (visibleRelations.get(relation) ?? 0) + 1);
    }
    return Object.freeze({
      ...projection,
      domainLegend: Object.freeze(projection.domainLegend.map(entry => Object.freeze({
        ...entry,
        visibleCount: visibleDomains.get(entry.canonicalId) ?? 0,
      }))),
      relationLegend: Object.freeze(projection.relationLegend.map(entry => Object.freeze({
        ...entry,
        visibleCount: visibleRelations.get(entry.rawEdgeType) ?? 0,
      }))),
    });
  }

  invalidateProfile(graphRevision: string, profileHash: string): void {
    const revisionToken = `\u0000${graphRevision}\u0000`;
    const profileSuffix = `\u0000${profileHash}`;
    this.projectionCache.deleteWhere(key => key.includes(revisionToken) && key.endsWith(profileSuffix));
  }

  clearCache(): void {
    this.normalizationCache.clear();
    this.projectionCache.clear();
    this.semanticProjectionCache.clear();
    this.currentRevisionBySource.clear();
  }

  cacheStats(): GraphVisualProjectionCacheStats {
    return {
      normalizationEntries: this.normalizationCache.size,
      projectionEntries: this.projectionCache.size,
    };
  }

  private computeProjection(
    graph: GenericGraphModel,
    profile: GraphVisualProfile,
    profileHash: string,
    context: GraphMetricNormalizationContext,
  ): Readonly<GraphVisualProjection> {
    const nodeStyles: Record<string, Readonly<NodeVisualStyle>> = Object.create(null);
    const edgeStyles: Record<string, Readonly<EdgeVisualStyle>> = Object.create(null);
    const domainLegend = new Map<string, MutableDomainLegend>();
    const relationLegend = new Map<string, MutableRelationLegend>();
    const domainByNode = new Map<string, string>();

    for (const node of graph.nodes) {
      const score = this.scoreService.scoreNode(node, profile, context);
      const visual = this.colorService.nodeVisual(node, profile);
      nodeStyles[node.id] = Object.freeze({
        nodeId: node.id,
        baseColor: visual.color,
        marker: visual.marker,
        baseSize: score.renderValue,
        score: score.normalizedScore,
        scoreState: score.state,
        availability: score.availability,
        breakdown: score.breakdown,
        highlightFactors: profile.highlightFactors,
      });
      domainByNode.set(node.id, visual.domain.canonicalId);
      const current = domainLegend.get(visual.domain.canonicalId);
      if (current) {
        current.totalCount += 1;
        current.visibleCount += 1;
        current.sumNodeScore += score.normalizedScore;
      } else {
        domainLegend.set(visual.domain.canonicalId, {
          canonicalId: visual.domain.canonicalId,
          label: visual.domain.label,
          color: visual.color,
          marker: visual.marker,
          totalCount: 1,
          visibleCount: 1,
          internalEdges: 0,
          outgoingExternalEdges: 0,
          incomingExternalEdges: 0,
          sumNodeScore: score.normalizedScore,
        });
      }
    }

    for (const edge of graph.edges) {
      const score = this.scoreService.scoreEdge(edge, profile, context);
      const visual = this.colorService.edgeVisual(edge, profile);
      edgeStyles[edge.id] = Object.freeze({
        edgeId: edge.id,
        baseColor: visual.color,
        marker: visual.marker,
        baseThickness: score.renderValue,
        score: score.normalizedScore,
        scoreState: score.state,
        availability: score.availability,
        breakdown: score.breakdown,
        highlightFactors: profile.highlightFactors,
      });
      const current = relationLegend.get(visual.rawEdgeType);
      if (current) {
        current.totalCount += 1;
        current.visibleCount += 1;
        current.multiplicitySum += edge.multiplicity ?? 1;
      } else {
        relationLegend.set(visual.rawEdgeType, {
          rawEdgeType: visual.rawEdgeType,
          label: visual.label,
          color: visual.color,
          marker: visual.marker,
          semanticallyKnown: visual.semanticallyKnown,
          totalCount: 1,
          visibleCount: 1,
          multiplicitySum: edge.multiplicity ?? 1,
        });
      }
      const sourceDomain = domainByNode.get(edge.source);
      const targetDomain = domainByNode.get(edge.target);
      if (!sourceDomain || !targetDomain) continue;
      if (sourceDomain === targetDomain) {
        const domain = domainLegend.get(sourceDomain);
        if (domain) domain.internalEdges += 1;
      } else {
        const source = domainLegend.get(sourceDomain);
        const target = domainLegend.get(targetDomain);
        if (source) source.outgoingExternalEdges += 1;
        if (target) target.incomingExternalEdges += 1;
      }
    }

    return Object.freeze({
      graphRevision: graph.metadata.graphRevision ?? '',
      profileHash,
      nodeStyles: Object.freeze(nodeStyles),
      edgeStyles: Object.freeze(edgeStyles),
      domainLegend: Object.freeze([...domainLegend.values()]
        .sort((left, right) => left.canonicalId.localeCompare(right.canonicalId))
        .map(entry => Object.freeze({ ...entry }))),
      relationLegend: Object.freeze([...relationLegend.values()]
        .sort((left, right) => left.rawEdgeType.localeCompare(right.rawEdgeType))
        .map(entry => Object.freeze({ ...entry }))),
    });
  }

  private rebindPresentation(
    projection: Readonly<GraphVisualProjection>,
    profile: GraphVisualProfile,
    profileHash: string,
  ): Readonly<GraphVisualProjection> {
    const currentFactors = Object.values(projection.nodeStyles)[0]?.highlightFactors;
    const factorsUnchanged = currentFactors
      && currentFactors.hover === profile.highlightFactors.hover
      && currentFactors.selected === profile.highlightFactors.selected
      && currentFactors.connected === profile.highlightFactors.connected;
    if (factorsUnchanged) {
      return Object.freeze({ ...projection, profileHash });
    }
    const nodeStyles = Object.fromEntries(Object.entries(projection.nodeStyles).map(([id, style]) => [
      id,
      Object.freeze({ ...style, highlightFactors: profile.highlightFactors }),
    ]));
    const edgeStyles = Object.fromEntries(Object.entries(projection.edgeStyles).map(([id, style]) => [
      id,
      Object.freeze({ ...style, highlightFactors: profile.highlightFactors }),
    ]));
    return Object.freeze({
      ...projection,
      profileHash,
      nodeStyles: Object.freeze(nodeStyles),
      edgeStyles: Object.freeze(edgeStyles),
    });
  }

  private invalidatePreviousRevision(sourceKey: string, revision: string): void {
    const previous = this.currentRevisionBySource.get(sourceKey);
    if (previous && previous !== revision) {
      const oldPrefix = `${sourceKey}\u0000${previous}`;
      this.normalizationCache.deleteWhere(key => key === oldPrefix);
      this.projectionCache.deleteWhere(key => key.startsWith(`${oldPrefix}\u0000`));
      this.semanticProjectionCache.deleteWhere(key => key.startsWith(`${oldPrefix}\u0000`));
    }
    this.currentRevisionBySource.set(sourceKey, revision);
  }
}
