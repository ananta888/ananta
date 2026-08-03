import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, Subject, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { ProjectContextService } from '../../../services/project-context.service';
import {
  OrganizationCompilePlan,
  OrganizationInstantiationGrant,
  OrganizationInstantiateResult,
} from '../models/organization-topology.models';
import { OrganizationApiClient, OrganizationPage } from './organization-api.client';
import { OrganizationTopologyStateService } from './organization-topology-state.service';

describe('OrganizationTopologyStateService project scope', () => {
  it('reloads for a project change and ignores the cancelled previous response', () => {
    const selectedProjectId = signal('project-alpha');
    const alphaBlueprints = new Subject<OrganizationPage<any>>();
    const alphaOrganizations = new Subject<OrganizationPage<any>>();
    const betaBlueprints = new Subject<OrganizationPage<any>>();
    const betaOrganizations = new Subject<OrganizationPage<any>>();
    const api = {
      listBlueprints: vi.fn((_hubUrl: string, projectId: string) => (
        projectId === 'project-alpha' ? alphaBlueprints : betaBlueprints
      )),
      listOrganizations: vi.fn((_hubUrl: string, projectId: string) => (
        projectId === 'project-alpha' ? alphaOrganizations : betaOrganizations
      )),
      topology: vi.fn((_hubUrl: string, organizationId: string) => of(topology(organizationId))),
    };

    TestBed.configureTestingModule({
      providers: [
        OrganizationTopologyStateService,
        { provide: OrganizationApiClient, useValue: api },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.example' }] },
        },
        { provide: ProjectContextService, useValue: { selectedProjectId } },
      ],
    });
    const state = TestBed.inject(OrganizationTopologyStateService);

    state.initialize();
    expect(api.listBlueprints).toHaveBeenCalledWith('https://hub.example', 'project-alpha');
    expect(api.listOrganizations).toHaveBeenCalledWith(
      'https://hub.example',
      'project-alpha',
      '',
      100,
    );

    selectedProjectId.set('project-beta');
    TestBed.flushEffects();
    expect(api.listBlueprints).toHaveBeenCalledWith('https://hub.example', 'project-beta');
    expect(state.organizations()).toEqual([]);

    betaBlueprints.next({ items: [blueprint('beta-blueprint')], next_cursor: null });
    betaBlueprints.complete();
    betaOrganizations.next({ items: [organization('beta-organization')], next_cursor: null });
    betaOrganizations.complete();

    alphaBlueprints.next({ items: [blueprint('alpha-blueprint')], next_cursor: null });
    alphaBlueprints.complete();
    alphaOrganizations.next({ items: [organization('alpha-organization')], next_cursor: null });
    alphaOrganizations.complete();

    expect(state.projectId()).toBe('project-beta');
    expect(state.blueprints().map(item => item.key)).toEqual(['beta-blueprint']);
    expect(state.organizations().map(item => item.id)).toEqual(['beta-organization']);
    expect(api.topology).toHaveBeenCalledWith(
      'https://hub.example',
      'beta-organization',
      expect.objectContaining({ include_runtime: true }),
    );
    expect(state.topology()?.organization_id).toBe('beta-organization');
  });

  it('fails locally before HTTP when no active project is selected', () => {
    const api = {
      listBlueprints: vi.fn(),
      listOrganizations: vi.fn(),
    };
    TestBed.configureTestingModule({
      providers: [
        OrganizationTopologyStateService,
        { provide: OrganizationApiClient, useValue: api },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.example' }] },
        },
        {
          provide: ProjectContextService,
          useValue: { selectedProjectId: signal('') },
        },
      ],
    });
    const state = TestBed.inject(OrganizationTopologyStateService);

    state.initialize();

    expect(api.listBlueprints).not.toHaveBeenCalled();
    expect(state.errorReasonCode()).toBe('project_id_required');
    expect(state.error()).toContain('Projekt');
  });

  it('cancels an in-flight custom compile when the project changes', () => {
    const selectedProjectId = signal('project-alpha');
    const admission = new Subject<any>();
    const compile = new Subject<any>();
    const api = {
      listBlueprints: vi.fn(() => of({ items: [], next_cursor: null })),
      listOrganizations: vi.fn(() => of({ items: [], next_cursor: null })),
      issueAdmissionException: vi.fn(() => admission),
      compileBlueprint: vi.fn(() => compile),
    };
    TestBed.configureTestingModule({
      providers: [
        OrganizationTopologyStateService,
        { provide: OrganizationApiClient, useValue: api },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.example' }] },
        },
        { provide: ProjectContextService, useValue: { selectedProjectId } },
      ],
    });
    const state = TestBed.inject(OrganizationTopologyStateService);

    state.compileCustom(
      'enterprise-organization',
      '1.0.0',
      'Enterprise Organization',
      { delivery: 2 },
      'Targeted test composition',
    );
    admission.next({ status: 'issued', admission_exception_ref: 'admission-1' });
    expect(api.compileBlueprint).toHaveBeenCalledWith(
      'https://hub.example',
      'project-alpha',
      expect.objectContaining({ admission_exception_ref: 'admission-1' }),
    );

    selectedProjectId.set('project-beta');
    TestBed.flushEffects();
    compile.next({ organization_id: 'stale-alpha-organization' });

    expect(state.projectId()).toBe('project-beta');
    expect(state.compilePlan()).toBeNull();
  });

  it('invalidates topology and layout state when the organization changes', () => {
    const selectedProjectId = signal('project-alpha');
    const alphaTopology = new Subject<any>();
    const betaTopology = new Subject<any>();
    const api = {
      listBlueprints: vi.fn(() => of({ items: [], next_cursor: null })),
      listOrganizations: vi.fn(() => of({
        items: [organization('organization-alpha'), organization('organization-beta')],
        next_cursor: null,
      })),
      topology: vi.fn((_hubUrl: string, organizationId: string) => (
        organizationId === 'organization-alpha' ? alphaTopology : betaTopology
      )),
    };
    TestBed.configureTestingModule({
      providers: [
        OrganizationTopologyStateService,
        { provide: OrganizationApiClient, useValue: api },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.example' }] },
        },
        { provide: ProjectContextService, useValue: { selectedProjectId } },
      ],
    });
    const state = TestBed.inject(OrganizationTopologyStateService);

    state.initialize();
    state.updateLayout({ node_id: 'alpha-node', x: 12, y: 24 });
    state.selectOrganization('organization-beta');
    alphaTopology.next(topology('organization-alpha'));
    betaTopology.next(topology('organization-beta'));

    expect(state.selectedOrganizationId()).toBe('organization-beta');
    expect(state.topology()?.organization_id).toBe('organization-beta');
    expect(state.layoutPreferences().size).toBe(0);
  });

  it('accepts only a server receipt bound to the active project, plan and policy', () => {
    const plan = compilePlan();
    const issueInstantiationGrant = vi.fn(() => of(instantiationGrant(plan)));
    const { state } = grantState({ issueInstantiationGrant });
    state.compilePlan.set(plan);

    state.issueInstantiationGrant(600);

    expect(issueInstantiationGrant).toHaveBeenCalledWith(
      'https://hub.example',
      'project-alpha',
      plan,
      expect.stringMatching(/^organization-instantiation-grant:/),
      600,
    );
    expect(state.instantiationGrant()?.grant_id).toBe('opgrant-precreation-1');
    expect(state.hasValidInstantiationGrant()).toBe(true);
  });

  it('fails closed for mismatched grant receipts and never instantiates with them', () => {
    const plan = compilePlan();
    const instantiate = vi.fn();
    const issueInstantiationGrant = vi.fn(() => of(instantiationGrant(plan, {
      project_id: 'project-beta',
    })));
    const { state } = grantState({ issueInstantiationGrant, instantiate });
    state.compilePlan.set(plan);

    state.issueInstantiationGrant();

    expect(state.instantiationGrant()).toBeNull();
    expect(state.hasValidInstantiationGrant()).toBe(false);
    expect(state.errorReasonCode()).toBe('organization_instantiation_grant_binding_invalid');

    const mismatches: readonly OrganizationInstantiationGrant[] = [
      instantiationGrant(plan, { project_id: 'project-beta' }),
      instantiationGrant(plan, { plan_digest: 'd'.repeat(64) }),
      instantiationGrant(plan, { policy_hash: 'e'.repeat(64) }),
      instantiationGrant(plan, { expires_at: Math.floor(Date.now() / 1000) + 4 }),
    ];
    for (const mismatch of mismatches) {
      state.instantiationGrant.set(mismatch);
      expect(state.hasValidInstantiationGrant()).toBe(false);
    }

    state.instantiate();

    expect(instantiate).not.toHaveBeenCalled();
    expect(state.instantiationGrant()).toBeNull();
    expect(state.errorReasonCode()).toBe('organization_instantiation_grant_required');
  });

  it('does not request a grant for an expired compile receipt', () => {
    const issueInstantiationGrant = vi.fn();
    const { state } = grantState({ issueInstantiationGrant });
    state.compilePlan.set({
      ...compilePlan(),
      expires_at: '2000-01-01T00:00:00Z',
    });

    state.issueInstantiationGrant();

    expect(issueInstantiationGrant).not.toHaveBeenCalled();
    expect(state.instantiationGrant()).toBeNull();
    expect(state.errorReasonCode()).toBe('organization_compile_plan_expired');
  });

  it('invalidates the compile plan and its precreation grant together', () => {
    const selectedProjectId = signal('project-alpha');
    const plan = compilePlan();
    const { state } = grantState({}, selectedProjectId);
    state.compilePlan.set(plan);
    state.instantiationGrant.set(instantiationGrant(plan));
    expect(state.hasValidInstantiationGrant()).toBe(true);

    state.discardCompilePlan();

    expect(state.compilePlan()).toBeNull();
    expect(state.instantiationGrant()).toBeNull();
    expect(state.hasValidInstantiationGrant()).toBe(false);

    state.compilePlan.set(plan);
    state.instantiationGrant.set(instantiationGrant(plan));
    selectedProjectId.set('project-beta');
    TestBed.flushEffects();

    expect(state.compilePlan()).toBeNull();
    expect(state.instantiationGrant()).toBeNull();
  });

  it('reuses stable issue and instantiate idempotency keys after transient failures', () => {
    const plan = compilePlan();
    const ambiguousIssueError = () => ({
      status: 408,
      error: { reason_code: 'organization_gateway_timeout' },
    });
    const transientApplyError = () => ({
      status: 503,
      error: { reason_code: 'organization_hub_temporarily_unavailable' },
    });
    const issueInstantiationGrant = vi.fn(() => throwError(ambiguousIssueError));
    const instantiate = vi.fn(() => throwError(transientApplyError));
    const { state, acquireSelectionLock } = grantState({ issueInstantiationGrant, instantiate });
    state.compilePlan.set(plan);

    state.issueInstantiationGrant();
    state.issueInstantiationGrant();

    expect(issueInstantiationGrant).toHaveBeenCalledTimes(2);
    const firstIssueKey = issueInstantiationGrant.mock.calls[0][3];
    const secondIssueKey = issueInstantiationGrant.mock.calls[1][3];
    expect(firstIssueKey).toMatch(/^organization-instantiation-grant:/);
    expect(secondIssueKey).toBe(firstIssueKey);

    state.instantiationGrant.set(instantiationGrant(plan));
    state.instantiate();
    state.instantiationGrant.set(instantiationGrant(plan, { expires_at: 0 }));
    state.instantiate();

    expect(instantiate).toHaveBeenCalledTimes(2);
    const firstApplyKey = instantiate.mock.calls[0][3];
    const secondApplyKey = instantiate.mock.calls[1][3];
    expect(firstApplyKey).toMatch(/^organization-instantiate:/);
    expect(secondApplyKey).toBe(firstApplyKey);
    expect(acquireSelectionLock).toHaveBeenCalledOnce();
    expect(acquireSelectionLock.mock.results[0].value).not.toHaveBeenCalled();
    expect(state.hasValidInstantiationGrant()).toBe(false);
    expect(state.instantiationOutcomeUncertain()).toBe(true);
    expect(state.instantiationPending()).toBe(true);
    expect(state.discardCompilePlan()).toBe(false);
    expect(state.compilePlan()).toBe(plan);
  });

  it('invalidates grant validity reactively at the expiry boundary', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
    const plan = compilePlan();
    const issueInstantiationGrant = vi.fn(() => of(instantiationGrant(plan, {
      expires_at: Math.floor(Date.now() / 1000) + 6,
    })));
    const { state } = grantState({ issueInstantiationGrant });
    state.compilePlan.set(plan);

    try {
      state.issueInstantiationGrant();
      expect(state.hasValidInstantiationGrant()).toBe(true);

      vi.advanceTimersByTime(1_100);

      expect(state.hasValidInstantiationGrant()).toBe(false);
    } finally {
      state.discardCompilePlan();
      vi.useRealTimers();
    }
  });

  it('discards terminal stale plans instead of retrying them', () => {
    const plan = compilePlan();
    const instantiate = vi.fn(() => throwError(() => ({
      status: 412,
      error: { reason_code: 'organization_definition_revision_stale' },
    })));
    const { state } = grantState({ instantiate });
    state.compilePlan.set(plan);
    state.instantiationGrant.set(instantiationGrant(plan));

    state.instantiate();

    expect(state.compilePlan()).toBeNull();
    expect(state.instantiationGrant()).toBeNull();
    expect(state.errorReasonCode()).toBe('organization_definition_revision_stale');
  });

  it('releases the project lock and invalidates the grant after a definitive grant error', () => {
    const plan = compilePlan();
    const instantiate = vi.fn(() => throwError(() => ({
      status: 403,
      error: { reason_code: 'organization_precreation_admin_grant_invalid' },
    })));
    const { state, acquireSelectionLock } = grantState({ instantiate });
    state.compilePlan.set(plan);
    state.instantiationGrant.set(instantiationGrant(plan));

    state.instantiate();

    expect(acquireSelectionLock.mock.results[0].value).toHaveBeenCalledOnce();
    expect(state.instantiationPending()).toBe(false);
    expect(state.instantiationOutcomeUncertain()).toBe(false);
    expect(state.compilePlan()).toBe(plan);
    expect(state.instantiationGrant()).toBeNull();
    expect(state.errorReasonCode()).toBe('organization_precreation_admin_grant_invalid');
  });

  it('releases the project lock but retains the receipt after a definitive ordinary 4xx', () => {
    const plan = compilePlan();
    const grant = instantiationGrant(plan);
    const instantiate = vi.fn(() => throwError(() => ({
      status: 422,
      error: { reason_code: 'organization_contract_invalid' },
    })));
    const { state, acquireSelectionLock } = grantState({ instantiate });
    state.compilePlan.set(plan);
    state.instantiationGrant.set(grant);

    state.instantiate();

    expect(acquireSelectionLock.mock.results[0].value).toHaveBeenCalledOnce();
    expect(state.instantiationPending()).toBe(false);
    expect(state.instantiationOutcomeUncertain()).toBe(false);
    expect(state.compilePlan()).toBe(plan);
    expect(state.instantiationGrant()).toBe(grant);
    expect(state.errorReasonCode()).toBe('organization_contract_invalid');
  });

  it('stores the real organization admin grant after successful instantiation', () => {
    const plan = compilePlan();
    const precreationGrant = instantiationGrant(plan);
    const result: OrganizationInstantiateResult = {
      organization: organization('organization-created', 'project-alpha'),
      unit_ids: ['unit-1'],
      team_ids: ['team-1'],
      role_slot_ids: ['role-slot-1'],
      relation_ids: ['relation-1'],
      organization_admin_grant_id: 'organization-admin-grant-1',
      topology_snapshot_hash: 'snapshot-created-1',
      replayed: false,
    };
    const instantiate = vi.fn(() => of(result));
    const { state, acquireSelectionLock } = grantState({ instantiate });
    state.compilePlan.set(plan);
    state.instantiationGrant.set(precreationGrant);

    state.instantiate();

    expect(acquireSelectionLock).toHaveBeenCalledWith(
      'organization-instantiation',
      expect.stringContaining('Projektwechsel'),
    );
    expect(acquireSelectionLock.mock.results[0].value).toHaveBeenCalledOnce();
    expect(instantiate).toHaveBeenCalledWith(
      'https://hub.example',
      'project-alpha',
      {
        compile_plan: plan,
        title: plan.title,
        admin_grant: precreationGrant.grant_id,
      },
      expect.stringMatching(/^organization-instantiate:/),
    );
    expect(state.compilePlan()).toBeNull();
    expect(state.instantiationGrant()).toBeNull();
    expect(state.selectedOrganizationId()).toBe('organization-created');
    expect(state.selectedOrganizationAdminGrant()).toBe('organization-admin-grant-1');
    expect(state.organizationAdminGrants().get('organization-created')).not.toBe(
      precreationGrant.grant_id,
    );
  });
});

function grantState(
  overrides: Readonly<Record<string, unknown>> = {},
  selectedProjectId = signal('project-alpha'),
): {
  state: OrganizationTopologyStateService;
  api: Readonly<Record<string, unknown>>;
  acquireSelectionLock: ReturnType<typeof vi.fn>;
} {
  const acquireSelectionLock = vi.fn(() => vi.fn());
  const api = {
    listBlueprints: vi.fn(() => of({ items: [], next_cursor: null })),
    listOrganizations: vi.fn(() => of({ items: [], next_cursor: null })),
    topology: vi.fn((_hubUrl: string, organizationId: string) => of(topology(organizationId))),
    issueInstantiationGrant: vi.fn(),
    instantiate: vi.fn(),
    ...overrides,
  };
  TestBed.configureTestingModule({
    providers: [
      OrganizationTopologyStateService,
      { provide: OrganizationApiClient, useValue: api },
      {
        provide: AgentDirectoryService,
        useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.example' }] },
      },
      {
        provide: ProjectContextService,
        useValue: { selectedProjectId, acquireSelectionLock },
      },
    ],
  });
  return {
    state: TestBed.inject(OrganizationTopologyStateService),
    api,
    acquireSelectionLock,
  };
}

function compilePlan(): OrganizationCompilePlan {
  return {
    blueprint_key: 'enterprise_scrum_organization',
    blueprint_version: '1',
    title: 'Enterprise Product Organization',
    organization_id: 'organization-candidate-1',
    definition_ref: 'enterprise_scrum_organization@1',
    definition_revision: 'revision-1',
    plan_digest: 'a'.repeat(64),
    compile_token: 'compile-token-1',
    expires_at: '2099-01-01T00:00:00Z',
    admin_policy_hash: 'b'.repeat(64),
    composition_mode: 'standard',
    team_count: 8,
    unit_count: 12,
    hierarchy_edge_count: 11,
    relation_edge_count: 7,
    role_slot_count: 16,
    planned_writes: ['organization_instance'],
    capability_gaps: [],
    unfilled_required_slots: [],
    budget_assumptions: {},
    diagnostics: [],
    limits: {
      revision: 'limits-1',
      policy_hash: 'c'.repeat(64),
      max_teams: 10,
      max_units: 20,
      max_role_slots: 100,
      max_assignments: 100,
      max_relations: 100,
      max_patch_operations: 50,
      max_page_size: 100,
      max_depth: 10,
      max_render_nodes: 500,
      max_render_edges: 1_000,
    },
  };
}

function instantiationGrant(
  plan: OrganizationCompilePlan,
  overrides: Partial<OrganizationInstantiationGrant> = {},
): OrganizationInstantiationGrant {
  return {
    grant_id: 'opgrant-precreation-1',
    grant_kind: 'instantiate',
    tenant_id: 'tenant-alpha',
    project_id: 'project-alpha',
    principal_id: 'principal-alpha',
    plan_digest: plan.plan_digest,
    policy_hash: plan.admin_policy_hash,
    expires_at: Math.floor(Date.now() / 1000) + 900,
    replayed: false,
    ...overrides,
  };
}

function blueprint(key: string): any {
  return {
    key,
    definition_key: key,
    version: '1.0.0',
    title: key,
    team_count: 8,
    standard: true,
    test_only: false,
    revision: 'revision-1',
    supported_team_counts: [8],
    custom_team_count_min: 2,
    custom_team_count_max: 10,
    custom_team_blueprints: [],
  };
}

function organization(id: string, projectId = 'project-beta'): any {
  return {
    id,
    key: id,
    title: id,
    lifecycle: 'active',
    definition_revision: 'revision-1',
    snapshot_hash: 'snapshot-1',
    team_count: 8,
    unit_count: 3,
    project_id: projectId,
    lock_version: 1,
    revision: 'revision-1',
  };
}

function topology(organizationId: string): any {
  return {
    organization_id: organizationId,
    definition_revision: 'revision-1',
    snapshot_hash: 'snapshot-1',
    nodes: [],
    edges: [],
    runtime_overlay: null,
    diagnostics: [],
    limits: { max_page_size: 100 },
    next_cursor: null,
    truncated: false,
  };
}
