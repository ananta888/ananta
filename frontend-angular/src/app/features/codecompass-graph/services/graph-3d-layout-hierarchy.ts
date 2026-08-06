import { RenderGraph } from '../../graph-rendering/models/render-graph.models';

interface ParentCandidate {
  readonly parentId: string;
  readonly childId: string;
  readonly priority: number;
  readonly edgeId: string;
}

export interface Graph3dHierarchyNode {
  readonly id: string;
  readonly depth: number;
  readonly order: number;
}

export interface Graph3dHierarchy {
  readonly nodes: readonly Graph3dHierarchyNode[];
  readonly maxDepth: number;
}

/**
 * Builds a deterministic, acyclic layout forest from structural relations.
 * It contains no rendering concerns and is shared by hierarchical and radial
 * projections so both modes explain the same notion of graph depth.
 */
export class Graph3dLayoutHierarchyBuilder {
  private readonly cache = new WeakMap<RenderGraph, Readonly<Graph3dHierarchy>>();

  build(graph: RenderGraph): Readonly<Graph3dHierarchy> {
    const cached = this.cache.get(graph);
    if (cached) return cached;

    const nodes = [...graph.nodes].sort((left, right) => left.id.localeCompare(right.id));
    const nodeIds = new Set(nodes.map(node => node.id));
    const candidates = new Map<string, ParentCandidate[]>();

    for (const edge of [...graph.edges].sort((left, right) => left.id.localeCompare(right.id))) {
      const relation = this.hierarchyRelation(edge.kind, edge.sourceId, edge.targetId);
      if (
        !relation
        || relation.parentId === relation.childId
        || !nodeIds.has(relation.parentId)
        || !nodeIds.has(relation.childId)
      ) {
        continue;
      }
      const values = candidates.get(relation.childId) ?? [];
      values.push({ ...relation, edgeId: edge.id });
      candidates.set(relation.childId, values);
    }

    const parentByChild = new Map<string, string>();
    for (const node of nodes) {
      const ranked = [...(candidates.get(node.id) ?? [])].sort((left, right) => (
        left.priority - right.priority
        || left.parentId.localeCompare(right.parentId)
        || left.edgeId.localeCompare(right.edgeId)
      ));
      const accepted = ranked.find(candidate => !this.createsCycle(
        candidate.parentId,
        candidate.childId,
        parentByChild,
      ));
      if (accepted) parentByChild.set(node.id, accepted.parentId);
    }

    const childrenByParent = new Map<string, string[]>();
    parentByChild.forEach((parentId, childId) => {
      const children = childrenByParent.get(parentId) ?? [];
      children.push(childId);
      childrenByParent.set(parentId, children);
    });
    childrenByParent.forEach(children => children.sort((left, right) => left.localeCompare(right)));

    const roots = nodes
      .filter(node => !parentByChild.has(node.id))
      .map(node => node.id);
    const projectedById = new Map<string, Readonly<Graph3dHierarchyNode>>();
    const stack = [...roots]
      .reverse()
      .map(id => ({ id, depth: 0 }));
    let order = 0;
    while (stack.length) {
      const current = stack.pop()!;
      if (projectedById.has(current.id)) continue;
      projectedById.set(current.id, Object.freeze({
        id: current.id,
        depth: current.depth,
        order: order++,
      }));
      const children = childrenByParent.get(current.id) ?? [];
      for (let index = children.length - 1; index >= 0; index -= 1) {
        stack.push({ id: children[index], depth: current.depth + 1 });
      }
    }

    // The parent selection is cycle-safe, so every node normally belongs to a
    // root. Keeping this defensive branch makes malformed future strategies
    // fail open as visible roots instead of dropping records.
    for (const node of nodes) {
      if (projectedById.has(node.id)) continue;
      projectedById.set(node.id, Object.freeze({ id: node.id, depth: 0, order: order++ }));
    }

    const projected = nodes.map(node => projectedById.get(node.id)!);
    const result = Object.freeze({
      nodes: Object.freeze(projected),
      maxDepth: projected.reduce((maximum, node) => Math.max(maximum, node.depth), 0),
    });
    this.cache.set(graph, result);
    return result;
  }

  private createsCycle(
    parentId: string,
    childId: string,
    parentByChild: ReadonlyMap<string, string>,
  ): boolean {
    let current: string | undefined = parentId;
    const visited = new Set<string>();
    while (current) {
      if (current === childId) return true;
      if (visited.has(current)) return true;
      visited.add(current);
      current = parentByChild.get(current);
    }
    return false;
  }

  private hierarchyRelation(
    rawKind: string,
    sourceId: string,
    targetId: string,
  ): Omit<ParentCandidate, 'edgeId'> | null {
    const kind = rawKind.trim().toLowerCase();
    if (kind.startsWith('child_of_') || kind === 'belongs_to') {
      return { parentId: targetId, childId: sourceId, priority: 2 };
    }
    if (kind === 'contains_directory') {
      return { parentId: sourceId, childId: targetId, priority: 0 };
    }
    if (kind === 'contains_file') {
      return { parentId: sourceId, childId: targetId, priority: 1 };
    }
    if (
      kind.startsWith('contains_')
      || kind.startsWith('declares_')
      || kind === 'contains'
      || kind === 'declares'
      || kind === 'parent_child'
    ) {
      return { parentId: sourceId, childId: targetId, priority: 3 };
    }
    return null;
  }
}
