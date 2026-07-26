import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output, Signal, WritableSignal, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  DatasetSummary,
  TrainingBaseModel,
  TrainingGpuProfile,
} from '../model-training/model-training.models';

import {
  CONTEXT_RECOVERY_STRATEGIES,
  ContextRecoveryStrategy,
  FallbackGroupSummary,
  ModelProfileSummary,
  ModelRoutingConfig,
  SkillProfile,
  TaskKindInfo,
  ValidationIssue,
  VpEdge,
  VpGraph,
  VpStep,
  VpRuntimeOverlay,
} from './visual-process-api.service';
import {
  VpNodeDefinition,
  VpNodeFieldDefinition,
  VpNodeFieldOption,
} from './vp-node-definition-registry.service';
import { canonicalVpJson } from './vp-assistant-context.service';
import { VpNodeFieldRendererComponent } from './vp-node-field-renderer.component';
import { VpResourceOptionProvider, VpResourceOptionSnapshot } from './vp-resource-option-provider';
import { VpTrainingNodeExtensionComponent } from './vp-training-node-extension.component';

@Component({
  selector: 'app-vp-step-inspector',
  standalone: true,
  imports: [FormsModule, VpNodeFieldRendererComponent, VpTrainingNodeExtensionComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './vp-step-inspector.component.html',
  styleUrls: ['./visual-process-editor.component.scss'],
})
export class VpStepInspectorComponent {
  private readonly resourceOptions = inject(VpResourceOptionProvider);
  @Input() mode: 'edit'|'runtime-readonly' = 'edit';
  @Input() runtimeOverlay: VpRuntimeOverlay|null = null;
  @Input({ required: true }) graph!: WritableSignal<VpGraph>;
  @Input() graphMutator?: (label: string, mutator: (graph: VpGraph) => void, coalesceKey?: string) => void;
  @Input({ required: true }) selectedId!: WritableSignal<string | null>;
  @Input({ required: true }) taskKindList!: Signal<TaskKindInfo[]>;
  @Input({ required: true }) skillProfiles!: Signal<SkillProfile[]>;
  @Input({ required: true }) modelProfiles!: Signal<ModelProfileSummary[]>;
  @Input({ required: true }) fallbackGroups!: Signal<Record<string, FallbackGroupSummary>>;
  @Input() trainingDatasets: readonly DatasetSummary[] = [];
  @Input() trainingProfiles: readonly TrainingGpuProfile[] = [];
  @Input() trainingBaseModels: readonly TrainingBaseModel[] = [];
  @Input({ required: true }) artifactKinds: string[] = [];
  @Input({ required: true }) edgeKinds: string[] = [];
  @Input({ required: true }) encodingModes: string[] = [];
  @Input({ required: true }) ragChannels: string[] = [];
  private readonly nodeDefinitionRevision = signal(0);
  private _nodeDefinitions: readonly VpNodeDefinition[] = [];
  @Input() set nodeDefinitions(value: readonly VpNodeDefinition[]) {
    this._nodeDefinitions = value ?? [];
    this.nodeDefinitionRevision.update(revision => revision + 1);
  }
  get nodeDefinitions(): readonly VpNodeDefinition[] { return this._nodeDefinitions; }
  @Input() validationIssues: readonly ValidationIssue[] = [];
  @Output() changed = new EventEmitter<void>();
  @Output() policyRefreshRequested = new EventEmitter<void>();
  @Output() statusChanged = new EventEmitter<string>();
  @Output() traceRequested = new EventEmitter<{runId:string;stepId:string}>();

  readonly selectedStep = computed(() => this.graph().steps.find(step => step.id === this.selectedId()) ?? null);
  readonly selectedEdge = computed(() => this.graph().edges.find(edge => edge.id === this.selectedId()) ?? null);
  readonly selectedStepKindInfo = computed(() => {
    const step = this.selectedStep();
    return step ? this.taskKindList().find(kind => kind.id === step.kind) ?? null : null;
  });
  readonly selectedDefinition = computed(() => {
    this.nodeDefinitionRevision();
    const step = this.selectedStep();
    return step ? this.nodeDefinitions.find(definition => definition.kind === step.kind) ?? null : null;
  });
  readonly definitionFields = computed(() => this.selectedDefinition()?.fields ?? []);
  readonly expertMode = signal(false);
  readonly expressionError = computed(() => {
    const edge = this.selectedEdge();
    if (edge?.condition.kind !== 'expression') return null;
    const expression = String(edge.condition.expression ?? '').trim();
    if (!expression) return 'Ausdruck erforderlich';
    return /^[\w.\s!=<>&|()+\-*/'"]+$/.test(expression) ? null : 'Ungültige Zeichen im Ausdruck';
  });
  readonly kindGroups = computed(() => {
    const groups = new Map<string, TaskKindInfo[]>();
    for (const kind of this.taskKindList()) {
      const group = kind.group || 'General';
      groups.set(group, [...(groups.get(group) ?? []), kind]);
    }
    return Array.from(groups.entries()).map(([group, kinds]) => ({ group, kinds }));
  });
  readonly stepDescription = computed(() => String(this.selectedStep()?.metadata?.['description'] ?? ''));
  readonly stepRouting = computed<ModelRoutingConfig>(() => {
    const raw = this.selectedStep()?.metadata?.['model_routing'];
    return (raw && typeof raw === 'object' ? raw : {}) as ModelRoutingConfig;
  });
  readonly fallbackGroupIds = computed(() => Object.keys(this.fallbackGroups()));
  readonly recoveryStrategyOptions = CONTEXT_RECOVERY_STRATEGIES;

  kindOptionSuffix(kind: TaskKindInfo): string {
    if (kind.implementation_status === 'experimental') return ' [exp]';
    if (['stub', 'not_implemented'].includes(kind.implementation_status)) return ' [stub]';
    if (kind.implementation_status === 'design_only') return ' [design]';
    if (kind.implementation_state === 'registered_only') return ' [reg]';
    return kind.dispatch_capable ? '' : ' (ML)';
  }

  mutateSelectedStep(mutate: (step: VpStep) => void, label = 'Schritt ändern', coalesceKey?: string): void {
    const selected = this.selectedStep();
    if (!selected) return;
    this.applyGraphMutation(label, graph => {
      const index = graph.steps.findIndex(step => step.id === selected.id);
      if (index < 0) return;
      mutate(graph.steps[index]);
    }, coalesceKey);
    this.changed.emit();
  }

  onKindChange(kind: string): void {
    this.mutateSelectedStep(step => step.kind = kind);
    this.policyRefreshRequested.emit();
  }

  setStepDescription(value: string): void {
    this.mutateSelectedStep(step => step.metadata = { ...(step.metadata ?? {}), description: value }, 'Beschreibung ändern', `step:${this.selectedId()}:description`);
  }

  stepMeta(key: string): any { return this.selectedStep()?.metadata?.[key] ?? null; }
  setStepMeta(key: string, value: unknown): void {
    this.mutateSelectedStep(step => step.metadata = { ...(step.metadata ?? {}), [key]: value }, `${key} ändern`, `step:${this.selectedId()}:metadata.${key}`);
  }
  /** Read-only compatibility for pre-registry graphs; all writes use metadata.weight. */
  rerankerWeight(): number {
    return Number(this.stepMeta('weight') ?? this.stepMeta('reranker_weight') ?? 0.15);
  }
  setRerankerWeight(value: unknown): void { this.setStepMeta('weight', Number(value)); }
  setStepLabel(value: string): void { this.mutateSelectedStep(step => step.label = value, 'Label ändern', `step:${this.selectedId()}:label`); }
  setStepRole(value: string): void { this.mutateSelectedStep(step => step.role = value, 'Rolle ändern', `step:${this.selectedId()}:role`); }
  setStepSkillProfile(value: string): void { this.mutateSelectedStep(step => step.agent_skill_profile_id = value, 'Skill-Profil ändern'); }
  setStepGate(value: boolean): void { this.mutateSelectedStep(step => step.gate = value, 'Gate ändern'); }

  fieldValue(field: VpNodeFieldDefinition): unknown {
    const step = this.selectedStep();
    if (!step) return field.default ?? null;
    const value = this.valueAtPath(step, field.path);
    if (value === undefined && field.path === '/metadata/weight') {
      return step.metadata?.['reranker_weight'] ?? field.default ?? null;
    }
    return value ?? field.default ?? null;
  }

  fieldVisible(field: VpNodeFieldDefinition): boolean { return this.conditionMatches(field.visibleWhen); }

  fieldRequired(field: VpNodeFieldDefinition): boolean {
    return field.required === true || (!!field.requiredWhen && this.conditionMatches(field.requiredWhen));
  }

  setFieldValue(field: VpNodeFieldDefinition, value: unknown): void {
    if (field.readOnly) return;
    this.mutateSelectedStep(step => {
      const segments = this.pathSegments(field.path);
      let cursor = step as unknown as Record<string, unknown>;
      for (const segment of segments.slice(0, -1)) {
        const existing = cursor[segment];
        cursor[segment] = existing && typeof existing === 'object' ? { ...(existing as Record<string, unknown>) } : {};
        cursor = cursor[segment] as Record<string, unknown>;
      }
      const key = segments.at(-1)!;
      if (value === '' && !this.fieldRequired(field)) delete cursor[key];
      else cursor[key] = value;
    }, `${field.label} ändern`, `step:${this.selectedId()}:${field.path}`);
  }

  selectedStepJson(): string {
    const step = this.selectedStep();
    if (!step) return '{}';
    const { run_state: _runtimeState, ...definition } = step;
    const safeDefinition = this.redactForDisplay(definition);
    try { return JSON.stringify(JSON.parse(canonicalVpJson(safeDefinition)), null, 2); }
    catch { try { return JSON.stringify(safeDefinition, null, 2); } catch { return '{}'; } }
  }

  fieldOptions(field: VpNodeFieldDefinition): VpNodeFieldOption[] {
    if (field.options) return field.options;
    const resourceKey = this.resourceOptionKey(field);
    if (resourceKey) {
      const remote = this.resourceOptions.snapshot(resourceKey);
      if (remote.reason !== 'not_loaded' || remote.options.length) {
        return remote.options.map(option => ({
          label: option.stale ? `${option.label} (nicht mehr verfügbar)` : option.label,
          value: option.value,
          disabled: option.disabled,
        }));
      }
    }
    switch (field.optionSource) {
      case 'skills': return this.skillProfiles().map(item => ({ label: item.name, value: item.id }));
      case 'models': return this.modelProfiles().map(item => ({ label: item.profile_id, value: item.profile_id }));
      case 'fallback-groups': return Object.keys(this.fallbackGroups()).map(value => ({ label: value, value }));
      case 'training-datasets': return this.trainingDatasets.map(item => ({ label: `${item.name} · ${item.id}`, value: item.id }));
      case 'training-profiles': return this.trainingProfiles.map(item => ({ label: item.id, value: item.id }));
      case 'training-base-models': return this.trainingBaseModels.map(item => ({ label: item.id, value: item.id }));
      case 'rag-channels': return this.ragChannels.map(value => ({ label: value, value }));
      case 'encoding-modes': return this.encodingModes.map(value => ({ label: value, value }));
      default: return [];
    }
  }

  fieldUsesOptionProvider(field: VpNodeFieldDefinition): boolean {
    return !!this.resourceOptionKey(field);
  }

  resourceOptionState(field: VpNodeFieldDefinition): VpResourceOptionSnapshot | null {
    const key = this.resourceOptionKey(field);
    return key ? this.resourceOptions.snapshot(key) : null;
  }

  refreshResourceOptions(field: VpNodeFieldDefinition): void {
    const key = this.resourceOptionKey(field);
    if (key) this.resourceOptions.refresh(key);
  }

  fieldIssues(field: VpNodeFieldDefinition): readonly ValidationIssue[] {
    const stepId = this.selectedId();
    return this.validationIssues.filter(issue => issue.step_id === stepId && (
      issue.path === field.path || issue.path === `/steps/${stepId}${field.path}`
    ));
  }

  setStepRoutingField(key: keyof ModelRoutingConfig, value: unknown): void {
    this.mutateSelectedStep(step => {
      const metadata = { ...(step.metadata ?? {}) };
      const routing = { ...((metadata['model_routing'] as ModelRoutingConfig | undefined) ?? {}) };
      if (value === '' || value === null || value === undefined) delete (routing as Record<string, unknown>)[key as string];
      else (routing as Record<string, unknown>)[key as string] = value;
      if (Object.keys(routing).length) metadata['model_routing'] = routing;
      else delete metadata['model_routing'];
      step.metadata = metadata;
    });
  }

  recoveryStrategyEnabled(strategy: ContextRecoveryStrategy): boolean {
    return (this.stepRouting().context_recovery_strategies ?? []).includes(strategy);
  }

  toggleRecoveryStrategy(strategy: ContextRecoveryStrategy, enabled: boolean): void {
    const selected = new Set(this.stepRouting().context_recovery_strategies ?? []);
    if (enabled) selected.add(strategy);
    else selected.delete(strategy);
    if (
      (strategy === 'segment_planning' || strategy === 'propose_task_plan')
      && enabled
    ) {
      selected.add('require_approval');
    }
    if (strategy === 'require_approval' && !enabled) {
      selected.delete('segment_planning');
      selected.delete('propose_task_plan');
    }
    this.setStepRoutingField(
      'context_recovery_strategies',
      CONTEXT_RECOVERY_STRATEGIES.filter(item => selected.has(item)),
    );
    if (
      (strategy === 'segment_planning' || strategy === 'propose_task_plan')
      && enabled
    ) {
      this.setStepRoutingField('require_approval_for_generated_plan', true);
    }
  }

  clearStepRouting(): void {
    this.mutateSelectedStep(step => {
      const metadata = { ...(step.metadata ?? {}) };
      delete metadata['model_routing'];
      step.metadata = metadata;
    });
  }

  applyModelRoutingPreset(kind: 'local' | 'cheap_cloud' | 'strong'): void {
    const profileId = kind === 'local'
      ? 'local_ollama_phi4_mini'
      : kind === 'cheap_cloud'
        ? 'openrouter_gemma3_4b_cheap_json'
        : 'openrouter_qwen3_30b_a3b_stronger';
    this.setStepRoutingField('preferred_profile_id', profileId);
    this.setStepRoutingField('fallback_group_id', kind === 'local' ? 'local_phi_to_gemma_reasoning' : undefined);
    this.setStepRoutingField('allow_cloud', kind !== 'local');
    if (kind === 'local') {
      this.setStepRoutingField('context_recovery_strategies', [
        'compact_context', 'segment_planning', 'propose_task_plan', 'require_approval', 'stop',
      ]);
      this.setStepRoutingField('require_approval_for_generated_plan', true);
    }
    this.statusChanged.emit(`Model-Profil "${profileId}" gesetzt`);
  }

  mutateEdge(mutate: (edge: VpEdge) => void, label = 'Kante ändern'): void {
    const selected = this.selectedEdge();
    if (!selected) return;
    this.applyGraphMutation(label, graph => {
      const index = graph.edges.findIndex(edge => edge.id === selected.id);
      if (index >= 0) mutate(graph.edges[index]);
    });
    this.changed.emit();
  }

  private applyGraphMutation(label: string, mutator: (graph: VpGraph) => void, coalesceKey?: string): void {
    if (this.graphMutator) {
      this.graphMutator(label, mutator, coalesceKey);
      return;
    }
    this.graph.update(graph => {
      const copy = structuredClone(graph);
      mutator(copy);
      return copy;
    });
  }

  private pathSegments(path: string): string[] {
    if (!path.startsWith('/')) return path.split('.').filter(Boolean);
    return path.split('/').slice(1).map(segment => segment.replaceAll('~1', '/').replaceAll('~0', '~'));
  }

  private resourceOptionKey(field: VpNodeFieldDefinition): string | null {
    return field.optionSource ?? ({
      skill_profile: 'skills', model_profile: 'models', training_dataset: 'training-datasets',
      training_profile: 'training-profiles', fallback_group: 'fallback-groups', process_reference: 'processes',
      rag_channel: 'rag-channels',
    } as Record<string, string>)[field.resourceType ?? ''] ?? null;
  }

  private valueAtPath(root: unknown, path: string): unknown {
    return this.pathSegments(path).reduce<unknown>((current, segment) => {
      if (!current || typeof current !== 'object') return undefined;
      return (current as Record<string, unknown>)[segment];
    }, root);
  }

  private conditionMatches(condition: VpNodeFieldDefinition['visibleWhen']): boolean {
    if (!condition) return true;
    const value = this.valueAtPath(this.selectedStep(), condition.path);
    if (condition.exists !== undefined && (value !== undefined && value !== null) !== condition.exists) return false;
    if (Object.prototype.hasOwnProperty.call(condition, 'equals') && !this.sameConditionValue(value, condition.equals)) return false;
    if (condition.equalsAny && !condition.equalsAny.some(candidate => this.sameConditionValue(value, candidate))) return false;
    if (Object.prototype.hasOwnProperty.call(condition, 'notEquals') && this.sameConditionValue(value, condition.notEquals)) return false;
    if (condition.notEqualsAny?.some(candidate => this.sameConditionValue(value, candidate))) return false;
    return true;
  }

  private sameConditionValue(left: unknown, right: unknown): boolean {
    if (Object.is(left, right)) return true;
    if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
    try { return canonicalVpJson(left) === canonicalVpJson(right); } catch { return false; }
  }

  private redactForDisplay(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(item => this.redactForDisplay(item));
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => {
      const normalized = key.toLocaleLowerCase('en-US').replaceAll('-', '_');
      const secret = ['api_key', 'apikey', 'access_token', 'refresh_token', 'password', 'credential', 'client_secret', 'private_key'].includes(normalized)
        && !normalized.endsWith('_secret_ref');
      return [key, secret ? '[REDACTED]' : this.redactForDisplay(item)];
    }));
  }

  setLoopPolicy(field: 'kind' | 'condition' | 'break_on_output' | 'max_iterations', value: unknown): void {
    this.mutateEdge(edge => {
      edge.condition.loop_policy ??= { kind: 'fixed', max_iterations: 3 };
      (edge.condition.loop_policy as any)[field] = value;
    });
  }
  setEdgeLabel(value: string): void { this.mutateEdge(edge => edge.label = value || undefined); }
  setEdgeConditionKind(value: string): void { this.mutateEdge(edge => edge.condition.kind = value as any); }
  setEdgeExpression(value: string): void { this.mutateEdge(edge => edge.condition.expression = value); }
  setEdgeOutputName(value: string): void { this.mutateEdge(edge => edge.condition.output_name = value); }
  mutateIOInput(index: number, field: string, value: unknown): void {
    this.mutateSelectedStep(step => (step.io.inputs[index] as any)[field] = value);
  }
  mutateIOOutput(index: number, field: string, value: unknown): void {
    this.mutateSelectedStep(step => (step.io.outputs[index] as any)[field] = value);
  }
  addInput(): void { this.mutateSelectedStep(step => step.io.inputs.push({ name: 'input', kind: 'text', required: true })); }
  removeInput(index: number): void { this.mutateSelectedStep(step => step.io.inputs.splice(index, 1)); }
  addOutput(): void { this.mutateSelectedStep(step => step.io.outputs.push({ name: 'output', kind: 'text', required: false })); }
  removeOutput(index: number): void { this.mutateSelectedStep(step => step.io.outputs.splice(index, 1)); }

  applyProfile(profileId: string): void {
    const profile = this.skillProfiles().find(candidate => candidate.id === profileId);
    if (!this.selectedStep()) return this.statusChanged.emit('Wähle zuerst einen Schritt aus');
    this.mutateSelectedStep(step => {
      step.agent_skill_profile_id = profileId;
      if (profile?.task_kinds?.[0]) step.kind = profile.task_kinds[0];
    });
    this.statusChanged.emit(`Profil "${profileId}" angewendet`);
    this.policyRefreshRequested.emit();
  }

  stepLabel(id: string): string { return this.graph().steps.find(step => step.id === id)?.label ?? id; }
}
