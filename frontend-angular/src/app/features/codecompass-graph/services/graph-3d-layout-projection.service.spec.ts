import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { RenderGraph } from '../../graph-rendering/models/render-graph.models';
import { Graph3dLayoutHierarchyBuilder } from './graph-3d-layout-hierarchy';
import { Graph3dLayoutProjectionService } from './graph-3d-layout-projection.service';

const GRAPH: RenderGraph = {
  nodes: [
    { id: 'repo', label: 'Repository', kind: 'repository', tooltip: 'Repository' },
    { id: 'dir', label: 'src', kind: 'directory', tooltip: 'src' },
    { id: 'file', label: 'main.py', kind: 'source_file', tooltip: 'main.py' },
    { id: 'type', label: 'Runner', kind: 'python_class', tooltip: 'Runner' },
    { id: 'method', label: 'run', kind: 'python_method', tooltip: 'run' },
    { id: 'detached', label: 'helper', kind: 'python_function', tooltip: 'helper' },
  ],
  edges: [
    { id: 'repo-dir', sourceId: 'repo', targetId: 'dir', kind: 'contains_directory', label: 'contains', tooltip: '' },
    { id: 'dir-file', sourceId: 'dir', targetId: 'file', kind: 'contains_file', label: 'contains', tooltip: '' },
    { id: 'file-type', sourceId: 'file', targetId: 'type', kind: 'declares', label: 'declares', tooltip: '' },
    { id: 'type-method', sourceId: 'type', targetId: 'method', kind: 'contains_method', label: 'contains', tooltip: '' },
    { id: 'method-helper', sourceId: 'method', targetId: 'detached', kind: 'calls', label: 'calls', tooltip: '' },
  ],
};

function service(): Graph3dLayoutProjectionService {
  TestBed.configureTestingModule({});
  return TestBed.inject(Graph3dLayoutProjectionService);
}

function position(graph: RenderGraph, nodeId: string) {
  const value = graph.nodes.find(node => node.id === nodeId)?.position;
  if (!value) throw new Error(`position missing for ${nodeId}`);
  return value;
}

describe('Graph3dLayoutProjectionService', () => {
  it('reuses the immutable hierarchy projection for the same render graph', () => {
    const hierarchy = new Graph3dLayoutHierarchyBuilder();

    expect(hierarchy.build(GRAPH)).toBe(hierarchy.build(GRAPH));
  });

  it('keeps force mode unconstrained and preserves the complete graph population', () => {
    const projected = service().project(GRAPH, 'force');

    expect(projected.nodes.map(node => node.id)).toEqual(GRAPH.nodes.map(node => node.id));
    expect(projected.edges).toBe(GRAPH.edges);
    expect(projected.nodes.every(node => node.position === undefined)).toBe(true);
  });

  it('places contains and semantic descendants on deterministic hierarchy levels', () => {
    const layouts = service();
    const first = layouts.project(GRAPH, 'hierarchical');
    const second = layouts.project(GRAPH, 'hierarchical');

    expect(position(first, 'repo').y).toBe(0);
    expect(position(first, 'dir').y).toBeLessThan(position(first, 'repo').y);
    expect(position(first, 'file').y).toBeLessThan(position(first, 'dir').y);
    expect(position(first, 'type').y).toBeLessThan(position(first, 'file').y);
    expect(position(first, 'method').y).toBeLessThan(position(first, 'type').y);
    expect(first.nodes.map(node => node.position)).toEqual(second.nodes.map(node => node.position));
    expect(first.nodes).toHaveLength(GRAPH.nodes.length);
    expect(first.edges).toBe(GRAPH.edges);
  });

  it('uses hierarchy depth as a stable, monotonically increasing radial distance', () => {
    const layouts = service();
    const first = layouts.project(GRAPH, 'radial');
    const second = layouts.project(GRAPH, 'radial');
    const radius = (nodeId: string) => {
      const value = position(first, nodeId);
      return Math.hypot(value.x, value.y, value.z);
    };

    expect(radius('repo')).toBeGreaterThan(0);
    expect(radius('detached')).toBeCloseTo(radius('repo'));
    expect(radius('dir')).toBeGreaterThan(radius('repo'));
    expect(radius('file')).toBeGreaterThan(radius('dir'));
    expect(radius('type')).toBeGreaterThan(radius('file'));
    expect(radius('method')).toBeGreaterThan(radius('type'));
    expect(first.nodes.map(node => node.position)).toEqual(second.nodes.map(node => node.position));
    expect(first.nodes).toHaveLength(GRAPH.nodes.length);
    expect(first.edges).toHaveLength(GRAPH.edges.length);
  });

  it('lays out exactly the already filtered graph without restoring or dropping records', () => {
    const filtered: RenderGraph = {
      nodes: GRAPH.nodes.filter(node => node.id === 'file' || node.id === 'type'),
      edges: GRAPH.edges.filter(edge => edge.id === 'file-type'),
    };
    const layouts = service();

    for (const mode of ['hierarchical', 'radial'] as const) {
      const projected = layouts.project(filtered, mode);
      expect(projected.nodes.map(node => node.id)).toEqual(['file', 'type']);
      expect(projected.edges.map(edge => edge.id)).toEqual(['file-type']);
    }

    const hierarchical = layouts.project(filtered, 'hierarchical');
    expect(position(hierarchical, 'file').y).toBe(0);
    expect(position(hierarchical, 'type').y).toBe(-110);
    const radial = layouts.project(filtered, 'radial');
    expect(Math.hypot(...coordinates(position(radial, 'file')))).toBe(0);
    expect(Math.hypot(...coordinates(position(radial, 'type')))).toBe(105);
  });

  it('centres a detached node as the root of its visible filtered forest', () => {
    const detached: RenderGraph = {
      nodes: [{
        id: 'function', label: 'function', kind: 'python_function', tooltip: 'function',
      }],
      edges: [],
    };
    const layouts = service();

    expect(position(layouts.project(detached, 'hierarchical'), 'function')).toMatchObject({
      x: 0, y: 0, z: 0,
    });
    expect(position(layouts.project(detached, 'radial'), 'function')).toMatchObject({
      x: 0, y: 0, z: 0,
    });
  });

  it('keeps ten thousand radial nodes complete, deterministic and spatially bounded', () => {
    const large: RenderGraph = {
      nodes: Array.from({ length: 10_000 }, (_, index) => ({
        id: `node-${index.toString().padStart(5, '0')}`,
        label: `Node ${index}`,
        kind: 'python_function',
        tooltip: `Node ${index}`,
      })),
      edges: [],
    };
    const layouts = service();
    const first = layouts.project(large, 'radial');
    const second = layouts.project(large, 'radial');
    const firstPositions = first.nodes.map(node => node.position!);

    expect(first.nodes).toHaveLength(10_000);
    expect(first.edges).toHaveLength(0);
    expect(firstPositions).toEqual(second.nodes.map(node => node.position));
    expect(firstPositions.every(value => (
      Number.isFinite(value.x) && Number.isFinite(value.y) && Number.isFinite(value.z)
    ))).toBe(true);
    expect(Math.max(...firstPositions.flatMap(value => (
      [Math.abs(value.x), Math.abs(value.y), Math.abs(value.z)]
    )))).toBeLessThan(2_000);
    expect(new Set(firstPositions.map(value => (
      `${value.x}:${value.y}:${value.z}`
    ))).size).toBe(10_000);
  });

  it('removes fixed coordinates when switching back to force without changing data', () => {
    const layouts = service();
    const radial = layouts.project(GRAPH, 'radial');
    const force = layouts.project(radial, 'force');

    expect(force.nodes.map(node => node.id)).toEqual(radial.nodes.map(node => node.id));
    expect(force.edges).toBe(radial.edges);
    expect(force.nodes.every(node => node.position === undefined)).toBe(true);
  });
});

function coordinates(value: { x: number; y: number; z: number }): [number, number, number] {
  return [value.x, value.y, value.z];
}
