import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, of, Subject } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { OrganizationApiClient } from '../services/organization-api.client';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { OrganizationRoleActivationComponent } from './organization-role-activation.component';
import {
  OrganizationRoleActivationMap,
  OrganizationRoleActivationProducesInputEdge,
} from './organization-role-activation.models';

describe('OrganizationRoleActivationComponent', () => {
  it('explains declared Hub routing without presenting unobserved runtime work as fact', () => {
    const state = fakeState();
    const roleActivationMap = vi.fn(() => of(activationMap()));
    configure(state, roleActivationMap);

    const fixture = TestBed.createComponent(OrganizationRoleActivationComponent);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent;
    expect(roleActivationMap).toHaveBeenCalledWith('http://hub.test', 'organization-1');
    expect(state.loadPlanning).toHaveBeenCalledOnce();
    expect(text).toContain('Nur der Hub darf daraus zur Laufzeit einen Task');
    expect(text).toContain('Eignung und freie Kapazität bleiben unbekannt');
    expect(text).toContain('keine exakt revisionsgebundenen Workflow-Tasks gefunden');
    expect(text).toContain('nach Abschluss von team:team-1/workflow:lean_delivery_workflow@1/step:frame');
    expect(text).toContain('product_lead@1');
    expect(text).toContain('1/1 aktive Assignments · desired_covered');
    expect(text).toContain('deklariert Inputfluss');
    expect(text).toContain('deklariert Handoff');
    expect(text).toContain('direction_governs_delivery');
    expect(text).toContain('portfolio_product_owner@1 via lean_direction_goal_handoff@1');
    expect(text).toContain('optionales Track-Amendment');
    expect(text).toContain('Lokale Task-Abhängigkeiten');
    expect(text).toContain('unbekannt');
  });

  it('filters steps and renders only associated definition edges', () => {
    const state = fakeState();
    configure(state, () => of(activationMap()));
    const fixture = TestBed.createComponent(OrganizationRoleActivationComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    expect(component.facade.allSteps()).toHaveLength(2);
    expect(component.facade.visibleEdges()).toHaveLength(4);
    component.facade.roleFilter.set('product_engineer@1');
    fixture.detectChanges();

    expect(component.facade.visibleSteps()).toHaveLength(1);
    expect(component.facade.visibleEdges()).toHaveLength(3);
    expect(fixture.nativeElement.textContent).toContain('Implement product slice');
    expect(fixture.nativeElement.textContent).not.toContain('Frame product slice');
    expect(fixture.nativeElement.textContent).not.toContain('deklariert Gate');
  });

  it('renders task, Hub-routing and Worker/Lease facts as separate observations', () => {
    const state = fakeState();
    const base = activationMap();
    const team = base.teams[0];
    const [frame, implement] = team.workflow.steps;
    const model: OrganizationRoleActivationMap = {
      ...base,
      runtime_observation: {
        state: 'partial',
        reason_code: 'organization_role_activation_runtime_partially_observed',
        task_state_included: true,
      },
      summary: {
        ...base.summary,
        runtime_bound_step_count: 1,
        task_ready_step_count: 1,
        hub_routed_step_count: 1,
        worker_executing_step_count: 1,
      },
      teams: [{
        ...team,
        workflow: {
          ...team.workflow,
          steps: [{
            ...frame,
            activation: {
              ...frame.activation,
              runtime: observedRuntime('task-frame'),
            },
          }, implement],
        },
      }],
    };
    configure(state, () => of(model));

    const fixture = TestBed.createComponent(OrganizationRoleActivationComponent);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;

    expect(text).toContain('Ist-Zustand teilweise beobachtet');
    expect(text).toContain('Lokale Task-Abhängigkeiten');
    expect(text).toContain('Hub routed');
    expect(text).toContain('Worker executing');
    expect(text).toContain('ja · beobachtet');
    expect(text).toContain('task-frame');
    expect(text).toContain('1 Job(s) · 1 aktive Lease(s)');

    fixture.componentInstance.facade.runtimeFilter.set('worker_executing');
    fixture.detectChanges();
    expect(fixture.componentInstance.facade.visibleSteps().map(item => item.step.step_id)).toEqual(['frame']);
    fixture.componentInstance.facade.runtimeFilter.set('unknown');
    fixture.detectChanges();
    expect(fixture.componentInstance.facade.visibleSteps().map(item => item.step.step_id)).toEqual(['implement']);
  });

  it('cancels an old organization request and accepts only the current response', () => {
    const state = fakeState();
    const first = new Subject<OrganizationRoleActivationMap>();
    const second = new Subject<OrganizationRoleActivationMap>();
    const roleActivationMap = vi.fn((_: string, organizationId: string): Observable<OrganizationRoleActivationMap> => (
      organizationId === 'organization-1' ? first : second
    ));
    configure(state, roleActivationMap);
    const fixture = TestBed.createComponent(OrganizationRoleActivationComponent);
    fixture.detectChanges();

    state.selectedOrganizationId.set('organization-2');
    fixture.detectChanges();
    first.next(activationMap('organization-1'));
    second.next(activationMap('organization-2'));
    fixture.detectChanges();

    expect(roleActivationMap.mock.calls.map(call => call[1])).toEqual([
      'organization-1',
      'organization-2',
    ]);
    expect(fixture.componentInstance.facade.model()?.organization_id).toBe('organization-2');
    expect(fixture.nativeElement.textContent).toContain('Delivery Cell organization-2');
    expect(state.loadPlanning).toHaveBeenCalledTimes(2);
  });

  it('rejects a malformed or foreign contract before rendering it', () => {
    const state = fakeState();
    const foreign = {
      ...activationMap('organization-2'),
      router_owner: 'worker',
    } as unknown as OrganizationRoleActivationMap;
    configure(state, () => of(foreign));
    const fixture = TestBed.createComponent(OrganizationRoleActivationComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.facade.model()).toBeNull();
    expect(fixture.componentInstance.facade.error()).toContain('organization_scope_mismatch');
    expect(fixture.nativeElement.textContent).not.toContain('Delivery Cell organization-2');
  });

  it('rejects a non-Hub router binding before rendering it', () => {
    const state = fakeState();
    const invalidRouter = {
      ...activationMap(),
      router_owner: 'worker',
    } as unknown as OrganizationRoleActivationMap;
    configure(state, () => of(invalidRouter));
    const fixture = TestBed.createComponent(OrganizationRoleActivationComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.facade.model()).toBeNull();
    expect(fixture.componentInstance.facade.error()).toContain('router_owner_invalid');
  });

  it('warns prominently when the topology snapshot is not revision-aligned', () => {
    const state = fakeState();
    const staleModel: OrganizationRoleActivationMap = {
      ...activationMap(),
      stale: true,
      snapshot_reason_code: 'organization_role_activation_snapshot_revision_mismatch',
    };
    configure(state, () => of(staleModel));
    const fixture = TestBed.createComponent(OrganizationRoleActivationComponent);
    fixture.detectChanges();

    const warning = fixture.nativeElement.querySelector('.snapshot-warning');
    expect(warning).not.toBeNull();
    expect(warning.getAttribute('role')).toBe('alert');
    expect(warning.textContent).toContain('Snapshot nicht revisionsgleich');
    expect(warning.textContent).toContain('organization_role_activation_snapshot_revision_mismatch');
  });

  it('limits the edge projection rendered by the template', () => {
    const state = fakeState();
    const model = activationMap();
    const source = model.edges.find(
      (edge): edge is OrganizationRoleActivationProducesInputEdge => edge.type === 'produces_input',
    )!;
    const edges = Array.from({ length: 205 }, (_, index) => ({
      ...source,
      edge_id: `activation-edge-${String(index).padStart(3, '0')}`,
    }));
    const largeModel: OrganizationRoleActivationMap = {
      ...model,
      summary: { ...model.summary, edge_count: edges.length },
      edges,
    };
    configure(state, () => of(largeModel));
    const fixture = TestBed.createComponent(OrganizationRoleActivationComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.facade.visibleEdges()).toHaveLength(200);
    expect(fixture.componentInstance.facade.edgesTruncated()).toBe(true);
    expect(fixture.nativeElement.textContent).toContain('Darstellung ist auf 200');
  });
});

function configure(
  state: ReturnType<typeof fakeState>,
  roleActivationMap: (
    hubUrl: string,
    organizationId: string,
  ) => Observable<OrganizationRoleActivationMap>,
): void {
  TestBed.configureTestingModule({
    imports: [OrganizationRoleActivationComponent],
    providers: [
      { provide: OrganizationTopologyStateService, useValue: state },
      { provide: OrganizationApiClient, useValue: { roleActivationMap } },
    ],
  });
}

function fakeState() {
  return {
    hubUrl: signal('http://hub.test'),
    selectedOrganizationId: signal<string | null>('organization-1'),
    planning: signal(null),
    loadPlanning: vi.fn(),
  };
}

function activationMap(organizationId = 'organization-1'): OrganizationRoleActivationMap {
  const teamUnitId = 'team-1';
  const workflowRef = 'lean_delivery_workflow@1';
  const frameRef = `team:${teamUnitId}/workflow:${workflowRef}/step:frame`;
  const implementRef = `team:${teamUnitId}/workflow:${workflowRef}/step:implement`;
  const selector = {
    team_blueprint_ref: 'lean_delivery_cell@1',
    cardinality: 1,
    routing: 'single',
  } as const;
  const target = {
    state: 'bound',
    reason_code: 'organization_role_activation_owning_team_bound',
    router_owner: 'hub' as const,
    candidate_team_unit_ids: [teamUnitId],
    bound_team_unit_ids: [teamUnitId],
  } as const;
  const coverage = {
    state: 'desired_covered',
    reason_code: 'organization_role_activation_assignment_desired_covered',
    required_count: 1,
    desired_count: 1,
    active_count: 1,
  } as const;
  const noGate = {
    required: false,
    acceptance_checks: [],
    approval_role_ref: null,
    independent_principal_required: false,
  } as const;
  return {
    schema: 'organization_role_activation_map.v1',
    organization_id: organizationId,
    definition_revision: 'd'.repeat(64),
    snapshot_hash: 's'.repeat(64),
    snapshot_revision: 3,
    stale: false,
    snapshot_reason_code: 'organization_role_activation_snapshot_current',
    router_owner: 'hub',
    runtime_observation: {
      state: 'not_observed',
      reason_code: 'organization_role_activation_runtime_not_observed',
      task_state_included: false,
    },
    summary: {
      active_team_count: 1,
      workflow_step_count: 2,
      edge_count: 4,
      unbound_step_count: 0,
      runtime_bound_step_count: 0,
      task_ready_step_count: 0,
      hub_routed_step_count: 0,
      worker_executing_step_count: 0,
    },
    teams: [{
      team_unit_id: teamUnitId,
      team_unit_key: 'delivery:001',
      team_name: `Delivery Cell ${organizationId}`,
      team_blueprint_ref: 'lean_delivery_cell@1',
      lifecycle: 'active',
      revision_binding: {
        team_blueprint_content_hash: 'a'.repeat(64),
        workflow_content_hash: 'b'.repeat(64),
      },
      workflow: {
        workflow_ref: workflowRef,
        mode: 'strict_gated',
        default_failure_policy: 'block',
        steps: [
          {
            step_id: 'frame',
            step_ref: frameRef,
            title: 'Frame product slice',
            task_kind: 'planning',
            owner_role_ref: 'product_lead@1',
            target_team_selector: selector,
            depends_on: [],
            inputs: ['company_goal'],
            outputs: ['delivery_brief'],
            gate: {
              required: true,
              acceptance_checks: ['delivery_brief_approved'],
              approval_role_ref: 'product_lead@1',
              independent_principal_required: false,
            },
            failure_policy: 'block',
            handoff_ref: null,
            target_resolution: target,
            role_binding: {
              state: 'bound',
              reason_code: 'organization_role_activation_owner_role_bound',
              owner_role_ref: 'product_lead@1',
              candidate_role_slot_ids: ['slot-lead'],
              bound_role_slot_ids: ['slot-lead'],
              assignment_coverage: coverage,
            },
            activation: {
              state: 'not_observed',
              reason_code: 'organization_role_activation_runtime_not_observed',
              router_owner: 'hub',
              rule: 'hub_route_on_workflow_start',
              reacts_to: [{ kind: 'hub_workflow_intake', source_ref: 'hub', source_owner_role_ref: null }],
              external_inputs: ['company_goal'],
              runtime: unknownRuntime(),
            },
          },
          {
            step_id: 'implement',
            step_ref: implementRef,
            title: 'Implement product slice',
            task_kind: 'coding',
            owner_role_ref: 'product_engineer@1',
            target_team_selector: selector,
            depends_on: ['frame'],
            inputs: ['delivery_brief'],
            outputs: ['increment'],
            gate: noGate,
            failure_policy: 'block',
            handoff_ref: null,
            target_resolution: target,
            role_binding: {
              state: 'bound',
              reason_code: 'organization_role_activation_owner_role_bound',
              owner_role_ref: 'product_engineer@1',
              candidate_role_slot_ids: ['slot-engineer'],
              bound_role_slot_ids: ['slot-engineer'],
              assignment_coverage: coverage,
            },
            activation: {
              state: 'not_observed',
              reason_code: 'organization_role_activation_runtime_not_observed',
              router_owner: 'hub',
              rule: 'hub_route_after_dependencies',
              reacts_to: [{
                kind: 'workflow_step_completion',
                source_ref: frameRef,
                source_owner_role_ref: 'product_lead@1',
              }],
              external_inputs: [],
              declared_input_sources: [{
                artifacts: ['company_goal'],
                source_step_ref: 'team:direction/workflow:direction@1/step:goal',
                source_owner_role_ref: 'portfolio_product_owner@1',
                source_team_unit_id: 'direction-team',
                handoff_ref: 'lean_direction_goal_handoff@1',
                relation_key: 'direction_governs_delivery',
              }],
              runtime: unknownRuntime(),
            },
          },
        ],
      },
    }],
    edges: [
      {
        edge_id: 'activation-edge-input',
        type: 'produces_input',
        source: { kind: 'workflow_step', ref: frameRef },
        target: { kind: 'workflow_step', ref: implementRef },
        reason_code: 'organization_workflow_artifact_flow_declared',
        metadata: { artifacts: ['delivery_brief'] },
      },
      {
        edge_id: 'activation-edge-dependency',
        type: 'unblocks',
        source: { kind: 'workflow_step', ref: frameRef },
        target: { kind: 'workflow_step', ref: implementRef },
        reason_code: 'organization_workflow_dependency_declared',
        metadata: {},
      },
      {
        edge_id: 'activation-edge-gate',
        type: 'requires_gate',
        source: { kind: 'workflow_step', ref: frameRef },
        target: { kind: 'role_template', ref: 'product_lead@1' },
        reason_code: 'organization_workflow_gate_declared',
        metadata: {
          acceptance_checks: ['delivery_brief_approved'],
          independent_principal_required: false,
        },
      },
      {
        edge_id: 'activation-edge-handoff',
        type: 'declares_handoff',
        source: { kind: 'team_unit', ref: 'direction-team' },
        target: { kind: 'team_unit', ref: teamUnitId },
        reason_code: 'organization_relation_handoff_declared',
        metadata: {
          relation_key: 'direction_governs_delivery',
          handoff_ref: 'lean_direction_goal_handoff@1',
          dependency_policy: 'declared',
          required_artifact_kinds: ['company_goal', 'accepted_requirements'],
          acceptance_gate_ref: 'lean_direction_goal_accepted@1',
        },
      },
    ],
  };
}

function unknownRuntime() {
  const fact = {
    state: 'unknown' as const,
    reason_code: 'organization_role_activation_unknown',
    observed_true_count: 0,
    observed_false_count: 0,
    unknown_count: 1,
  };
  return {
    binding: {
      state: 'unknown' as const,
      reason_code: 'organization_role_activation_exact_task_binding_missing',
      task_ids: [],
    },
    task_ready: fact,
    hub_routed: fact,
    worker_executing: fact,
    worker_job_count: 0,
    active_lease_count: 0,
  };
}

function observedRuntime(taskId: string) {
  const fact = {
    state: 'observed_true' as const,
    reason_code: 'organization_role_activation_observed_true',
    observed_true_count: 1,
    observed_false_count: 0,
    unknown_count: 0,
  };
  return {
    binding: {
      state: 'exact' as const,
      reason_code: 'organization_role_activation_exact_task_binding_observed',
      task_ids: [taskId],
    },
    task_ready: fact,
    hub_routed: fact,
    worker_executing: fact,
    worker_job_count: 1,
    active_lease_count: 1,
  };
}
