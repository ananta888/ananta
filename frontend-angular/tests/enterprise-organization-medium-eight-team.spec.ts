import { expect, test } from '@playwright/test';

import { loginFast } from './utils';

const organizationId = 'organization-medium-eight';
const definitionRevision = 'definition-revision-medium-eight';
const snapshotHash = 'snapshot-medium-eight';

const units = [
  { id: 'coordination', stable_key: 'portfolio', kind: 'coordination_unit', label: 'Portfolio Coordination', parent_id: 'organization', depth: 1 },
  { id: 'discovery', stable_key: 'discovery', kind: 'value_stream', label: 'Discovery Value Stream', parent_id: 'coordination', depth: 2 },
  { id: 'delivery', stable_key: 'delivery', kind: 'value_stream', label: 'Delivery Value Stream', parent_id: 'coordination', depth: 2 },
  { id: 'enablement', stable_key: 'enablement', kind: 'value_stream', label: 'Enablement Value Stream', parent_id: 'coordination', depth: 2 },
] as const;

const teams = [
  ['portfolio-team', 'portfolio_product_coordination', 'Portfolio Product Coordination', 'coordination'],
  ['research-team', 'research_and_discovery', 'Research and Discovery', 'discovery'],
  ['poc-team', 'proof_of_concept', 'Proof of Concept', 'discovery'],
  ['delivery-team-1', 'enterprise_product_delivery_scrum:001', 'Delivery Team 1', 'delivery'],
  ['delivery-team-2', 'enterprise_product_delivery_scrum:002', 'Delivery Team 2', 'delivery'],
  ['platform-team', 'platform_devops_sre', 'Platform DevOps SRE', 'enablement'],
  ['architecture-team', 'architecture_governance', 'Architecture Governance', 'enablement'],
  ['release-team', 'quality_security_release', 'Quality Security Release', 'enablement'],
] as const;

function topology() {
  const organization = {
    id: 'organization', stable_key: 'enterprise_scrum_organization', kind: 'organization',
    label: 'Enterprise Product Organization', parent_id: null, depth: 0, child_count: 1,
  };
  const unitNodes = units.map(unit => ({
    ...unit,
    child_count: unit.id === 'coordination'
      ? 3
      : teams.filter(team => team[3] === unit.id).length,
  }));
  const teamNodes = teams.map(([id, stable_key, label, parent_id]) => ({
    id, stable_key, kind: 'team', label, parent_id, depth: 3, child_count: 0, team_id: id,
  }));
  const nodes = [organization, ...unitNodes, ...teamNodes];
  const hierarchyEdges = nodes.filter(node => node.parent_id).map(node => ({
    id: `contains:${node.parent_id}:${node.id}`,
    namespace: 'hierarchy', kind: 'contains', source_id: node.parent_id, target_id: node.id,
  }));
  const organizationEdges = [
    ['portfolio-team', 'research-team', 'declared_dependency'],
    ['research-team', 'poc-team', 'handoff'],
    ['poc-team', 'architecture-team', 'handoff'],
    ['architecture-team', 'delivery-team-1', 'declared_dependency'],
    ['architecture-team', 'delivery-team-2', 'declared_dependency'],
    ['delivery-team-1', 'release-team', 'handoff'],
    ['delivery-team-2', 'release-team', 'handoff'],
    ['release-team', 'platform-team', 'handoff'],
  ].map(([source_id, target_id, kind], index) => ({
    id: `organization-edge-${index + 1}`, namespace: 'organization', kind, source_id, target_id,
  }));
  return {
    organization_id: organizationId,
    definition_revision: definitionRevision,
    snapshot_hash: snapshotHash,
    nodes,
    edges: [...hierarchyEdges, ...organizationEdges],
    runtime_overlay: {
      definition_revision: definitionRevision,
      snapshot_hash: snapshotHash,
      generated_at: '2026-08-02T12:00:00Z',
      stale: false,
      nodes: [
        {
          node_id: 'delivery-team-1',
          status: { state: 'active', label: 'In Arbeit', blocker_count: 1, gate_count: 1, handoff_count: 1, capacity_used: 2, capacity_limit: 4 },
          latest_artifacts: [{ artifact_id: 'artifact-delivery', version: '2', digest: 'artifact-digest', label: 'Verified increment' }],
        },
        { node_id: 'release-team', status: { state: 'blocked', label: 'Gate blockiert', blocker_count: 1, gate_count: 2, handoff_count: 2 } },
      ],
      edges: [{
        id: 'runtime:delivery:release', namespace: 'runtime', kind: 'runtime_task_dependency',
        source_id: 'delivery-team-1', target_id: 'release-team', read_only: true,
      }],
    },
    diagnostics: [{ severity: 'warning', reason_code: 'HANDOFF_NEEDS_CHANGES', message: 'Ein Handoff benötigt Nacharbeit.', node_ids: ['delivery-team-1', 'release-team'] }],
    limits: {
      revision: '1', policy_hash: 'limit-policy-hash', max_teams: 32, max_units: 128,
      max_role_slots: 1024, max_assignments: 2048, max_relations: 4096,
      max_patch_operations: 100, max_page_size: 100, max_depth: 8,
      max_render_nodes: 500, max_render_edges: 2000,
    },
    next_cursor: null,
    truncated: false,
  };
}

function planningReadModel(validProposalStatus = 'needs_approval') {
  return {
    organization_id: organizationId,
    definition_revision: definitionRevision,
    nodes: [
      { id: 'goal', kind: 'goal', label: 'Ship enterprise increment', status: 'executing', revision: 'goal-r1', digest: 'goal-d1' },
      { id: 'category', kind: 'category_todo', label: 'Research Category-Todo', status: 'promoted', revision: 'category-r2', digest: 'category-d2', parent_id: 'goal' },
      { id: 'track', kind: 'planning_track', label: 'Delivery Planning Track', status: 'adopted', revision: 'track-r3', digest: 'track-d3', parent_id: 'category', source_category_item_ids: ['CAT-01'] },
      { id: 'milestone', kind: 'milestone', label: 'Verified release', status: 'in_progress', parent_id: 'track' },
      { id: 'task', kind: 'task', label: 'Implement bounded follow-up', status: 'todo', parent_id: 'milestone', source_category_item_ids: ['CAT-01'] },
    ],
    proposals: [
      {
        proposal_id: 'proposal-valid', revision: 'proposal-r1', digest: 'proposal-d1',
        source_task_id: 'source-task-1', proposer_role_slot_id: 'researcher-slot',
        status: validProposalStatus, policy_hash: 'proposal-policy-hash',
        target_role_hint: 'backend-engineer', target_agent_hint: 'untrusted-hint',
        selected_role_slot_id: 'backend-slot', selected_team_id: 'delivery-team-2',
        approval_id: 'approval-proposal-1',
      },
      {
        proposal_id: 'proposal-rejected', revision: 'proposal-r1', digest: 'proposal-rejected-d1',
        source_task_id: 'source-task-2', proposer_role_slot_id: 'developer-slot',
        status: 'rejected', policy_hash: 'proposal-policy-hash', reason_code: 'proposal_policy_escalation_denied',
        target_agent_hint: 'forced-agent',
      },
    ],
    next_cursor: null,
  };
}

test.describe('enterprise organization medium eight-team reference', () => {
  test('keeps the sole full reference revision-bound across hierarchy, graph, planning, proposal, patch preview and export', async ({ page, request }) => {
    await loginFast(page, request);

    let proposalDecisionBody: Record<string, unknown> | undefined;
    let patchApplyCalled = false;

    await page.route('**/api/organization-blueprints*', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'success', data: { items: [{
        key: 'enterprise_scrum_organization', version: '1', title: 'Enterprise Scrum Organization',
        team_count: 8, standard: true, recommended: true, revision: definitionRevision,
      }], next_cursor: null } }),
    }));
    await page.route('**/api/organization-bundles/export*', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'success', data: {
        schema_version: '2.0',
        bundle_metadata: {
          export_kind: 'organization_definition_graph',
          portability: 'cross_tenant_project',
          root_definition_ref: 'enterprise_scrum_organization@1',
          instance_transport: 'excluded',
          assignment_transport: 'excluded',
        },
        organization_instances: [], include_assignments: false, assignments: [],
      } }),
    }));
    await page.route('**/api/organizations*', async route => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      if (path.endsWith('/topology')) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data: topology() }) });
        return;
      }
      if (path.endsWith('/planning')) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data: planningReadModel() }) });
        return;
      }
      if (path.includes('/proposals/proposal-valid/approve')) {
        proposalDecisionBody = route.request().postDataJSON();
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data: planningReadModel('accepted_as_plan_amendment') }) });
        return;
      }
      if (path.endsWith('/patches/preview')) {
        const requestBody = route.request().postDataJSON();
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data: {
          organization_id: organizationId, expected_revision: definitionRevision,
          patch_digest: 'discarded-patch-digest', expires_at: '2026-08-02T13:00:00Z',
          operations: requestBody.operations, planned_writes: ['organization_units:create:temporary-review-team'],
          diagnostics: [], limits: topology().limits, applicable: true,
        } }) });
        return;
      }
      if (path.endsWith('/patches/apply')) {
        patchApplyCalled = true;
        await route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
        return;
      }
      if (path === '/api/organizations') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data: { items: [{
          id: organizationId, key: 'enterprise_scrum_organization', title: 'Enterprise Product Organization', lifecycle: 'active',
          definition_revision: definitionRevision, snapshot_hash: snapshotHash, team_count: 8, unit_count: 12,
        }], next_cursor: null } }) });
        return;
      }
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    });

    await page.goto('/organizations', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Organisationen' })).toBeVisible();
    await expect(page.getByRole('treeitem')).toHaveCount(13);
    await expect(page.getByText('8 Teams', { exact: false }).first()).toBeVisible();
    await expect(page.getByText('revisionsgebunden')).toBeVisible();

    await page.getByRole('treeitem', { name: /Delivery Team 1/ }).click();
    await expect(page.getByRole('heading', { name: 'Inspector' })).toBeVisible();
    await expect(page.locator('.inspector').getByText('In Arbeit')).toBeVisible();
    await expect(page.getByText('Verified increment')).toBeVisible();
    await page.getByRole('tab', { name: 'Graph' }).click();
    await expect(page.getByRole('heading', { name: 'Graph' })).toBeVisible();
    await expect(page.locator('.node.selected')).toContainText('Delivery Team 1');
    await expect(page.locator('line.edge.organization')).toHaveCount(8);
    await expect(page.locator('line.edge.runtime')).toHaveCount(1);

    await page.getByRole('button', { name: 'Planung & Proposals' }).click();
    await expect(page.getByText('Research Category-Todo')).toBeVisible();
    await expect(page.getByText('Delivery Planning Track')).toBeVisible();
    await expect(page.getByText('proposal-rejected')).toBeVisible();
    await expect(page.getByText('proposal_policy_escalation_denied')).toBeVisible();
    await page.getByRole('button', { name: 'Gebunden freigeben' }).click();
    await expect.poll(() => proposalDecisionBody).toEqual({ expected_revision: 'proposal-r1', expected_digest: 'proposal-d1' });
    await expect(page.getByText('accepted_as_plan_amendment')).toBeVisible();

    await page.getByRole('button', { name: 'Ändern' }).click();
    await page.getByLabel('Parent-ID').fill('delivery');
    await page.getByLabel('Stabiler Schlüssel').fill('temporary-review-team');
    await page.getByLabel('Bezeichnung').fill('Temporary Review Team');
    await page.getByRole('button', { name: 'Draft prüfen' }).click();
    await expect(page.getByRole('heading', { name: 'Dry-run-Ergebnis' })).toBeVisible();
    await expect(page.getByText('discarded-patch-digest')).toBeVisible();
    await page.getByRole('button', { name: 'Verwerfen' }).click();
    expect(patchApplyCalled).toBeFalsy();

    await page.getByRole('button', { name: 'Topologie' }).click();
    await page.getByRole('tab', { name: 'Hierarchie' }).click();
    await expect(page.getByRole('treeitem')).toHaveCount(13);
    await expect(page.getByRole('treeitem', { name: /Temporary Review Team/ })).toHaveCount(0);

    await page.getByRole('button', { name: 'Import / Export' }).click();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Ausgewählte Organisation exportieren' }).click();
    expect((await download).suggestedFilename()).toBe('organization-definitions-enterprise_scrum_organization-1.bundle.v2.json');
    await expect(page.getByText('Redigiertes Bundle wurde exportiert.')).toBeVisible();
  });
});
