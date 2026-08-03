import { describe, expect, it } from 'vitest';

import {
  OrganizationTopologyEdge,
  OrganizationTopologyNode,
  OrganizationRuntimeOverlay,
} from '../models/organization-topology.models';
import { OrganizationGraphProjectionService } from './organization-graph-projection.service';
import { DEFAULT_ORGANIZATION_VISUAL_PROFILE } from './organization-visual-profile.models';

const NODES: readonly OrganizationTopologyNode[] = [
  { id: 'org', stable_key: 'org', kind: 'organization', label: 'Small Co', depth: 0, child_count: 2 },
  {
    id: 'chief', stable_key: 'slot-chief', kind: 'role_slot', label: 'Gigantic Chief Title', depth: 1, child_count: 1,
    metadata: { role_template_ref: 'role:lead', default_count: 1 },
  },
  {
    id: 'member', stable_key: 'slot-member', kind: 'role_slot', label: 'Developer', depth: 1, child_count: 0,
    metadata: { role_template_ref: 'role:member', default_count: 2 },
  },
  { id: 'agent', stable_key: 'assignment-a', kind: 'assignment', label: 'Agent A', depth: 2, child_count: 0 },
];

const EDGES: readonly OrganizationTopologyEdge[] = [
  { id: 'org-chief', namespace: 'hierarchy', kind: 'contains', source_id: 'org', target_id: 'chief' },
  { id: 'chief-agent', namespace: 'hierarchy', kind: 'contains', source_id: 'chief', target_id: 'agent' },
  { id: 'chief-member', namespace: 'organization', kind: 'governs', source_id: 'chief', target_id: 'member' },
];

describe('OrganizationGraphProjectionService', () => {
  const service = new OrganizationGraphProjectionService();

  it('preserves every node and edge and exposes a complete visual legend', () => {
    const projection = service.project(NODES, EDGES, null, DEFAULT_ORGANIZATION_VISUAL_PROFILE);

    expect(projection.graph.nodes).toHaveLength(NODES.length);
    expect(projection.graph.edges).toHaveLength(EDGES.length);
    expect(projection.graph.edges.find(edge => edge.id === 'chief-agent')?.kind).toBe('assignment');
    expect(projection.nodeLegend.reduce((sum, entry) => sum + entry.count, 0)).toBe(NODES.length);
    expect(projection.edgeLegend.reduce((sum, entry) => sum + entry.count, 0)).toBe(EDGES.length);
    expect(projection.edgeLegend.every(entry => (
      entry.minimumWidth <= entry.medianWidth && entry.medianWidth <= entry.maximumWidth
    ))).toBe(true);
    expect(projection.assignmentStatus).toEqual({
      observedRoleCount: 0,
      underAssignedRoleCount: 0,
      unknownRoleCount: 2,
    });
  });

  it('never infers leadership or importance from a role label', () => {
    const projection = service.project(NODES, EDGES, null, {
      ...DEFAULT_ORGANIZATION_VISUAL_PROFILE,
      nodeSizeMetric: 'leadership_scope',
      nodeColorMetric: 'leadership_scope',
    });

    expect(projection.nodeStyles['chief']).toEqual(projection.nodeStyles['member']);
  });

  it('applies only explicit stable-key or role-template overrides', () => {
    const projection = service.project(NODES, EDGES, null, {
      ...DEFAULT_ORGANIZATION_VISUAL_PROFILE,
      nodeSizeMetric: 'importance',
      nodeColorMetric: 'leadership_scope',
      roleOverrides: {
        'role:lead': { importance: 90, leadershipScope: 'organization', color: '#FF0000' },
        'slot-member': { importance: 10, leadershipScope: 'team', color: '#00FF00' },
      },
    });

    expect(projection.nodeStyles['chief'].color).toBe('#FF0000');
    expect(projection.nodeStyles['member'].color).toBe('#00FF00');
    expect(projection.nodeStyles['chief'].size).toBeGreaterThan(projection.nodeStyles['member'].size);
    expect(projection.nodeLegend.filter(entry => entry.label.includes('explizite Rollenfarbe')))
      .toHaveLength(2);
  });

  it('compares active assignments with desired defaults rather than maximum capacity', () => {
    const runtime: OrganizationRuntimeOverlay = {
      definition_revision: 'revision-a',
      snapshot_hash: 'snapshot-a',
      generated_at: '2026-08-03T00:00:00Z',
      stale: false,
      nodes: [
        { node_id: 'chief', status: { state: 'ready', label: 'Ready', capacity_used: 1, capacity_limit: 3 } },
        { node_id: 'member', status: { state: 'ready', label: 'Ready', capacity_used: 1, capacity_limit: 2 } },
      ],
      edges: [],
    };

    const projection = service.project(NODES, EDGES, runtime, DEFAULT_ORGANIZATION_VISUAL_PROFILE);

    expect(projection.assignmentStatus).toEqual({
      observedRoleCount: 2,
      underAssignedRoleCount: 1,
      unknownRoleCount: 0,
    });
  });

  it('disambiguates repeated role slots with their nearest team label and stable key', () => {
    const repeatedNodes: readonly OrganizationTopologyNode[] = [
      { id: 'team-a', stable_key: 'delivery:001', kind: 'team', label: 'Delivery Cell 1', depth: 1, child_count: 1 },
      { id: 'team-b', stable_key: 'delivery:002', kind: 'team', label: 'Delivery Cell 2', depth: 1, child_count: 1 },
      {
        id: 'lead-a', stable_key: 'delivery:001:product_lead', kind: 'role_slot', label: 'Product Lead',
        parent_id: 'team-a', depth: 2, child_count: 0, metadata: { default_count: 1 },
      },
      {
        id: 'lead-b', stable_key: 'delivery:002:product_lead', kind: 'role_slot', label: 'Product Lead',
        parent_id: 'team-b', depth: 2, child_count: 0, metadata: { default_count: 1 },
      },
    ];

    const projection = service.project(repeatedNodes, [], null, DEFAULT_ORGANIZATION_VISUAL_PROFILE);

    expect(projection.roleTargets).toEqual([
      expect.objectContaining({ key: 'delivery:001:product_lead', teamLabel: 'Delivery Cell 1' }),
      expect.objectContaining({ key: 'delivery:002:product_lead', teamLabel: 'Delivery Cell 2' }),
    ]);
  });

  it('reports active metrics and uses a kind-specific edge color fallback', () => {
    const projection = service.project(NODES, EDGES, null, {
      ...DEFAULT_ORGANIZATION_VISUAL_PROFILE,
      nodeColorMetric: 'importance',
      edgeColorMetric: 'kind',
      edgeStrengthMetric: 'fixed',
      edgeKindColors: {},
    });

    expect(projection.activeMetrics).toEqual({
      nodeColor: 'Explizite Wichtigkeit',
      edgeColor: 'Kantenart',
      edgeStrength: 'Einheitlich',
    });
    expect(projection.edgeStyles['org-chief'].color).toBe('#94A3B8');
    expect(projection.edgeStyles['chief-member'].color).toBe('#818CF8');
  });
});
