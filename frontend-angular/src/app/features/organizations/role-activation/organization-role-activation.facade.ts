import { computed, DestroyRef, effect, inject, Injectable, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { finalize, Subject, takeUntil } from 'rxjs';

import { OrganizationApiClient } from '../services/organization-api.client';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import {
  OrganizationRoleActivationMap,
  OrganizationRoleActivationStepView,
  roleActivationContractError,
} from './organization-role-activation.models';

const EDGE_RENDER_LIMIT = 200;

@Injectable()
export class OrganizationRoleActivationFacade {
  private readonly api = inject(OrganizationApiClient);
  private readonly topologyState = inject(OrganizationTopologyStateService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly cancelRequest = new Subject<void>();
  private requestSequence = 0;
  private activeScope = '';

  readonly model = signal<OrganizationRoleActivationMap | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly teamFilter = signal('');
  readonly workflowFilter = signal('');
  readonly roleFilter = signal('');
  readonly taskKindFilter = signal('');
  readonly bindingFilter = signal('');
  readonly runtimeFilter = signal('');
  readonly search = signal('');
  readonly runtimeStates = [
    { value: 'unknown', label: 'Ist-Zustand unbekannt' },
    { value: 'task_ready', label: 'Lokale Task-Abhängigkeiten erfüllt' },
    { value: 'hub_routed', label: 'Hub routed' },
    { value: 'worker_executing', label: 'Worker executing' },
    { value: 'observed_inactive', label: 'Beobachtet, derzeit inaktiv' },
  ] as const;

  readonly allSteps = computed<readonly OrganizationRoleActivationStepView[]>(() => (
    this.model()?.teams.flatMap(team => (
      team.workflow.steps.map(step => ({ team, step }))
    )) ?? []
  ));
  readonly visibleSteps = computed(() => {
    const team = this.teamFilter();
    const workflow = this.workflowFilter();
    const role = this.roleFilter();
    const taskKind = this.taskKindFilter();
    const binding = this.bindingFilter();
    const runtime = this.runtimeFilter();
    const search = this.search().trim().toLocaleLowerCase();
    return this.allSteps().filter(item => (
      (!team || item.team.team_unit_id === team)
      && (!workflow || item.team.workflow.workflow_ref === workflow)
      && (!role || item.step.owner_role_ref === role)
      && (!taskKind || item.step.task_kind === taskKind)
      && (!binding || item.step.role_binding.state === binding)
      && (!runtime || this.matchesRuntime(item, runtime))
      && (!search || this.searchableText(item).includes(search))
    ));
  });
  readonly teams = computed(() => this.model()?.teams ?? []);
  readonly workflows = computed(() => unique(
    this.model()?.teams.map(team => team.workflow.workflow_ref) ?? [],
  ));
  readonly roles = computed(() => unique(this.allSteps().map(item => item.step.owner_role_ref)));
  readonly taskKinds = computed(() => unique(this.allSteps().map(item => item.step.task_kind)));
  readonly bindingStates = computed(() => unique(
    this.allSteps().map(item => item.step.role_binding.state),
  ));
  readonly visibleEdges = computed(() => {
    const stepRefs = new Set(this.visibleSteps().map(item => item.step.step_ref));
    return (this.model()?.edges ?? []).filter(edge => (
      edge.type === 'declares_handoff'
      || stepRefs.has(edge.source.ref)
      || (edge.target.kind === 'workflow_step' && stepRefs.has(edge.target.ref))
    )).slice(0, EDGE_RENDER_LIMIT);
  });
  readonly edgesTruncated = computed(() => {
    const stepRefs = new Set(this.visibleSteps().map(item => item.step.step_ref));
    let matched = 0;
    for (const edge of this.model()?.edges ?? []) {
      if (edge.type === 'declares_handoff'
        || stepRefs.has(edge.source.ref)
        || (edge.target.kind === 'workflow_step' && stepRefs.has(edge.target.ref))) matched += 1;
      if (matched > EDGE_RENDER_LIMIT) return true;
    }
    return false;
  });

  constructor() {
    effect(() => {
      const hubUrl = this.topologyState.hubUrl();
      const organizationId = this.topologyState.selectedOrganizationId() ?? '';
      untracked(() => this.activateScope(hubUrl, organizationId));
    });
  }

  load(): void {
    const hubUrl = this.topologyState.hubUrl();
    const organizationId = this.topologyState.selectedOrganizationId();
    if (!hubUrl || !organizationId || this.loading()) return;
    this.request(hubUrl, organizationId);
  }

  private activateScope(hubUrl: string, organizationId: string): void {
    const scope = `${hubUrl}|${organizationId}`;
    if (scope === this.activeScope) return;
    this.activeScope = scope;
    this.cancelRequest.next();
    this.requestSequence += 1;
    this.loading.set(false);
    this.model.set(null);
    this.error.set('');
    this.resetFilters();
    if (hubUrl && organizationId) this.request(hubUrl, organizationId);
  }

  private request(hubUrl: string, organizationId: string): void {
    this.cancelRequest.next();
    const sequence = ++this.requestSequence;
    const scope = `${hubUrl}|${organizationId}`;
    this.activeScope = scope;
    this.loading.set(true);
    this.api.roleActivationMap(hubUrl, organizationId).pipe(
      takeUntil(this.cancelRequest),
      takeUntilDestroyed(this.destroyRef),
      finalize(() => {
        if (sequence === this.requestSequence) this.loading.set(false);
      }),
    ).subscribe({
      next: model => {
        if (sequence !== this.requestSequence
          || scope !== this.activeScope
          || hubUrl !== this.topologyState.hubUrl()
          || organizationId !== this.topologyState.selectedOrganizationId()) return;
        const contractError = roleActivationContractError(model, organizationId);
        if (contractError) {
          this.model.set(null);
          this.error.set(`Ungültiger Rollenaktivierungs-Contract: ${contractError}`);
          return;
        }
        this.model.set(model);
      },
      error: () => {
        if (sequence === this.requestSequence) {
          this.model.set(null);
          this.error.set('Rollenaktivierung konnte nicht geladen werden.');
        }
      },
    });
  }

  resetFilters(): void {
    this.teamFilter.set('');
    this.workflowFilter.set('');
    this.roleFilter.set('');
    this.taskKindFilter.set('');
    this.bindingFilter.set('');
    this.runtimeFilter.set('');
    this.search.set('');
  }

  private searchableText(item: OrganizationRoleActivationStepView): string {
    const { team, step } = item;
    return [
      team.team_name,
      team.team_unit_key,
      team.workflow.workflow_ref,
      step.step_id,
      step.title,
      step.task_kind,
      step.owner_role_ref,
      ...step.inputs,
      ...step.outputs,
      ...step.depends_on,
      step.activation.runtime.binding.state,
      step.activation.runtime.task_ready.state,
      step.activation.runtime.hub_routed.state,
      step.activation.runtime.worker_executing.state,
    ].join(' ').toLocaleLowerCase();
  }

  private matchesRuntime(item: OrganizationRoleActivationStepView, filter: string): boolean {
    const runtime = item.step.activation.runtime;
    if (filter === 'unknown') {
      return runtime.binding.state === 'unknown'
        || runtime.task_ready.state === 'unknown'
        || runtime.hub_routed.state === 'unknown'
        || runtime.worker_executing.state === 'unknown';
    }
    if (filter === 'task_ready') return runtime.task_ready.state === 'observed_true';
    if (filter === 'hub_routed') return runtime.hub_routed.state === 'observed_true';
    if (filter === 'worker_executing') return runtime.worker_executing.state === 'observed_true';
    return filter === 'observed_inactive'
      && runtime.binding.state === 'exact'
      && [runtime.task_ready, runtime.hub_routed, runtime.worker_executing]
        .every(fact => fact.state === 'observed_false');
  }
}

function unique(values: readonly string[]): readonly string[] {
  return [...new Set(values.filter(Boolean))].sort((left, right) => left.localeCompare(right));
}
