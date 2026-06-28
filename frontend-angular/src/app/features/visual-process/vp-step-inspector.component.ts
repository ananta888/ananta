import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output, Signal, WritableSignal, computed } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { SkillProfile, TaskKindInfo, VpEdge, VpGraph, VpStep } from './visual-process-api.service';

@Component({
  selector: 'app-vp-step-inspector',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './vp-step-inspector.component.html',
  styleUrls: ['./visual-process-editor.component.scss'],
})
export class VpStepInspectorComponent {
  @Input({ required: true }) graph!: WritableSignal<VpGraph>;
  @Input({ required: true }) selectedId!: WritableSignal<string | null>;
  @Input({ required: true }) taskKindList!: Signal<TaskKindInfo[]>;
  @Input({ required: true }) skillProfiles!: Signal<SkillProfile[]>;
  @Input({ required: true }) artifactKinds: string[] = [];
  @Input({ required: true }) edgeKinds: string[] = [];
  @Input({ required: true }) encodingModes: string[] = [];
  @Input({ required: true }) ragChannels: string[] = [];
  @Output() changed = new EventEmitter<void>();
  @Output() policyRefreshRequested = new EventEmitter<void>();
  @Output() statusChanged = new EventEmitter<string>();

  readonly selectedStep = computed(() => this.graph().steps.find(step => step.id === this.selectedId()) ?? null);
  readonly selectedEdge = computed(() => this.graph().edges.find(edge => edge.id === this.selectedId()) ?? null);
  readonly selectedStepKindInfo = computed(() => {
    const step = this.selectedStep();
    return step ? this.taskKindList().find(kind => kind.id === step.kind) ?? null : null;
  });
  readonly expressionError = computed(() => {
    const edge = this.selectedEdge();
    if (edge?.condition.kind !== 'expression') return null;
    const expression = String(edge.condition.expression ?? '').trim();
    if (!expression) return 'Ausdruck erforderlich';
    return /^[\w.\s!=<>&|()+\-*/'"]+$/.test(expression) ? null : 'Ungültige Zeichen im Ausdruck';
  });

  kindOptionSuffix(kind: TaskKindInfo): string {
    if (kind.implementation_status === 'experimental') return ' [exp]';
    if (['stub', 'not_implemented'].includes(kind.implementation_status)) return ' [stub]';
    if (kind.implementation_status === 'design_only') return ' [design]';
    if (kind.implementation_state === 'registered_only') return ' [reg]';
    return kind.dispatch_capable ? '' : ' (ML)';
  }

  mutateSelectedStep(mutate: (step: VpStep) => void): void {
    const selected = this.selectedStep();
    if (!selected) return;
    this.graph.update(graph => ({
      ...graph,
      steps: graph.steps.map(step => {
        if (step.id !== selected.id) return step;
        const copy = structuredClone(step);
        mutate(copy);
        return copy;
      }),
    }));
    this.changed.emit();
  }

  onKindChange(kind: string): void {
    this.mutateSelectedStep(step => step.kind = kind);
    this.policyRefreshRequested.emit();
  }

  setStepDescription(value: string): void {
    this.mutateSelectedStep(step => step.metadata = { ...(step.metadata ?? {}), description: value });
  }

  stepMeta(key: string): any { return this.selectedStep()?.metadata?.[key] ?? null; }
  setStepMeta(key: string, value: unknown): void {
    this.mutateSelectedStep(step => step.metadata = { ...(step.metadata ?? {}), [key]: value });
  }
  setStepLabel(value: string): void { this.mutateSelectedStep(step => step.label = value); }
  setStepRole(value: string): void { this.mutateSelectedStep(step => step.role = value); }
  setStepSkillProfile(value: string): void { this.mutateSelectedStep(step => step.agent_skill_profile_id = value); }
  setStepGate(value: boolean): void { this.mutateSelectedStep(step => step.gate = value); }

  isChannelSelected(channel: string): boolean {
    const channels = this.stepMeta('channels') as string[] | null;
    return channels ? channels.includes(channel) : ['dense', 'lexical'].includes(channel);
  }

  toggleChannel(channel: string, selected: boolean): void {
    const current = (this.stepMeta('channels') as string[] | null) ?? ['dense', 'lexical'];
    this.setStepMeta('channels', selected
      ? [...new Set([...current, channel])]
      : current.filter(item => item !== channel));
  }

  mutateEdge(mutate: (edge: VpEdge) => void): void {
    const selected = this.selectedEdge();
    if (!selected) return;
    this.graph.update(graph => ({
      ...graph,
      edges: graph.edges.map(edge => {
        if (edge.id !== selected.id) return edge;
        const copy = structuredClone(edge);
        mutate(copy);
        return copy;
      }),
    }));
    this.changed.emit();
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
