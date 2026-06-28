import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output, Signal, WritableSignal, computed, inject } from '@angular/core';
import { SlicePipe } from '@angular/common';

import { CodehugCanvasInteractionService } from '../services/codehug-canvas-interaction.service';
import { AnantaWorker, VpSkillProfile } from '../services/internals.service';
import {
  ARTIFACT_KINDS, BACKENDS, CAPABILITIES, VP_KINDS,
  type ArtifactBinding, type ArtifactSlot, type CanvasEdge, type CanvasNode,
  type EdgeCondition, type RoutingMode, type StepRouting,
} from './codehug-canvas-types';

@Component({
  selector: 'codehug-node-inspector',
  standalone: true,
  imports: [SlicePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './codehug-node-inspector.component.html',
  styleUrls: ['./codehug-internals.component.scss'],
})
export class CodehugNodeInspectorComponent {
  private readonly canvas = inject(CodehugCanvasInteractionService);

  @Input({ required: true }) nodes!: WritableSignal<CanvasNode[]>;
  @Input({ required: true }) edges!: WritableSignal<CanvasEdge[]>;
  @Input({ required: true }) selectedNodeId!: WritableSignal<string | null>;
  @Input({ required: true }) selectedEdgeId!: WritableSignal<string | null>;
  @Input({ required: true }) currentRoles!: Signal<string[]>;
  @Input({ required: true }) skillProfiles!: Signal<VpSkillProfile[]>;
  @Input({ required: true }) workers!: Signal<AnantaWorker[]>;
  @Input({ required: true }) detRunResult!: Signal<Record<string, unknown> | null>;
  @Input({ required: true }) detRunning!: Signal<boolean>;
  @Input() activeStepId: string | null = null;
  @Output() runDeterministic = new EventEmitter<CanvasNode>();

  readonly ARTIFACT_KINDS = ARTIFACT_KINDS;
  readonly BACKENDS = BACKENDS;
  readonly CAPABILITIES = CAPABILITIES;
  readonly VP_KINDS = VP_KINDS;
  readonly selectedNode = computed(() => this.nodes().find(node => node.id === this.selectedNodeId()) ?? null);
  readonly selectedEdge = computed(() => this.edges().find(edge => edge.id === this.selectedEdgeId()) ?? null);
  private nodeSequence = 1000;
  private edgeSequence = 1000;

  addNode(type: CanvasNode['type']): void {
    const cx = Number.isFinite(this.canvas.centerX()) ? this.canvas.centerX() : 300;
    const cy = Number.isFinite(this.canvas.centerY()) ? this.canvas.centerY() : 200;
    const id = `${type}-${++this.nodeSequence}`;
    const role = this.currentRoles()[this.nodes().filter(node => node.type === 'task').length % Math.max(1, this.currentRoles().length)] ?? '';
    const dimensions = ['fork', 'join'].includes(type) ? { w: 120, h: 52 } : { w: 220, h: 68 };
    const node: CanvasNode = {
      id, x: cx - dimensions.w / 2, y: cy - dimensions.h / 2, ...dimensions,
      type, title: this.defaultTitle(type), subtitle: '', role,
      priority: 'Medium', enabled: true, inputs: [], outputs: [],
      routing: ['task', 'det'].includes(type) ? { mode: 'auto' } : undefined,
      detSubtype: type === 'det' ? 'script' : undefined,
      failAction: ['det', 'gate', 'review'].includes(type) ? 'block' : undefined,
      gateSubtype: type === 'gate' ? 'auto-verify' : undefined,
    };
    this.nodes.update(nodes => [...nodes, node]);
    this.selectedNodeId.set(id);
  }

  addInput(nodeId: string): void {
    this.updateNode(nodeId, node => ({
      ...node,
      inputs: [...node.inputs, { name: `input_${node.inputs.length + 1}`, kind: 'text', required: true, description: '' }],
    }));
  }

  addOutput(nodeId: string): void {
    this.updateNode(nodeId, node => ({
      ...node,
      outputs: [...node.outputs, { name: `output_${node.outputs.length + 1}`, kind: 'text', required: false, description: '' }],
    }));
  }

  patchSlot(nodeId: string, field: 'inputs' | 'outputs', index: number, patch: Partial<ArtifactSlot>): void {
    this.updateNode(nodeId, node => {
      const slots = [...node[field]];
      slots[index] = { ...slots[index], ...patch };
      return { ...node, [field]: slots };
    });
  }

  removeSlot(nodeId: string, field: 'inputs' | 'outputs', index: number): void {
    this.updateNode(nodeId, node => ({ ...node, [field]: node[field].filter((_, current) => current !== index) }));
  }

  addArtifactBinding(edgeId: string, raw: string): void {
    const [outputName, inputName] = raw.split('=>');
    if (!outputName || !inputName) return;
    this.edges.update(edges => edges.map(edge => {
      if (edge.id !== edgeId) return edge;
      const bindings = [...(edge.bindings ?? [])];
      if (!bindings.some(item => item.outputName === outputName && item.inputName === inputName)) {
        bindings.push({ outputName, inputName });
      }
      return { ...edge, bindings, outputName: edge.outputName ?? outputName };
    }));
    this.syncInputBindings();
  }

  removeArtifactBinding(edgeId: string, binding: ArtifactBinding): void {
    this.edges.update(edges => edges.map(edge => edge.id === edgeId
      ? { ...edge, bindings: (edge.bindings ?? []).filter(item => item.outputName !== binding.outputName || item.inputName !== binding.inputName) }
      : edge));
    this.syncInputBindings();
  }

  availableBindingOptions(edge: CanvasEdge): Array<{ value: string; label: string }> {
    const source = this.nodes().find(node => node.id === edge.from);
    const target = this.nodes().find(node => node.id === edge.to);
    if (!source || !target) return [];
    const existing = new Set((edge.bindings ?? []).map(item => `${item.outputName}=>${item.inputName}`));
    return source.outputs.flatMap(output => target.inputs
      .filter(input => (input.kind === output.kind || input.kind === 'text' || output.kind === 'text')
        && !existing.has(`${output.name}=>${input.name}`))
      .map(input => ({ value: `${output.name}=>${input.name}`, label: `${output.name} → ${input.name}` })));
  }

  edgeBindingLabel(edge: CanvasEdge): string {
    const bindings = edge.bindings ?? [];
    return bindings.length === 1 ? `📦 ${bindings[0].outputName}` : bindings.length ? `📦 ${bindings.length} artifacts` : '';
  }

  setRoutingMode(nodeId: string, mode: RoutingMode): void {
    this.patchNode(nodeId, { routing: { mode, backend: 'ananta', capability: 'coder', workerName: '' } });
  }

  patchRouting(nodeId: string, patch: Partial<StepRouting>): void {
    const node = this.nodes().find(candidate => candidate.id === nodeId);
    this.patchNode(nodeId, { routing: { ...(node?.routing ?? { mode: 'auto' }), ...patch } });
  }

  patchNode(id: string, patch: Partial<CanvasNode>): void {
    this.updateNode(id, node => ({ ...node, ...patch }));
  }

  patchEdge(id: string, patch: Partial<CanvasEdge>): void {
    this.edges.update(edges => edges.map(edge => edge.id === id ? { ...edge, ...patch } : edge));
  }

  deleteNode(nodeId: string): void {
    this.nodes.update(nodes => nodes.filter(node => node.id !== nodeId));
    this.edges.update(edges => edges.filter(edge => edge.from !== nodeId && edge.to !== nodeId));
    this.selectedNodeId.set(null);
  }

  deleteEdge(edgeId: string): void {
    this.edges.update(edges => edges.filter(edge => edge.id !== edgeId));
    this.selectedEdgeId.set(null);
  }

  insertOnEdge(edgeId: string): void {
    const edge = this.edges().find(candidate => candidate.id === edgeId);
    if (!edge) return;
    const point = this.edgeMidpoint(edge);
    const id = `task-${++this.nodeSequence}`;
    this.nodes.update(nodes => [...nodes, {
      id, x: point.x - 110, y: point.y - 34, w: 220, h: 68,
      type: 'task', title: 'Eingefügter Schritt', subtitle: '',
      role: this.currentRoles()[0] ?? '', priority: 'Medium', enabled: true,
      routing: { mode: 'auto' }, inputs: [], outputs: [],
    }]);
    this.edges.update(edges => [
      ...edges.filter(candidate => candidate.id !== edgeId),
      { id: `e-${++this.edgeSequence}`, from: edge.from, to: id, condition: edge.condition },
      { id: `e-${++this.edgeSequence}`, from: id, to: edge.to, condition: 'always' as EdgeCondition },
    ]);
  }

  edgePath(edge: CanvasEdge): string {
    const source = this.nodes().find(node => node.id === edge.from);
    const target = this.nodes().find(node => node.id === edge.to);
    if (!source || !target) return '';
    if (edge.condition === 'back_edge') {
      const left = Math.min(source.x, target.x) - 60;
      return `M ${source.x} ${source.y + source.h / 2} C ${left} ${source.y + source.h / 2}, ${left} ${target.y + target.h / 2}, ${target.x} ${target.y + target.h / 2}`;
    }
    const x1 = source.x + source.w / 2, y1 = source.y + source.h;
    const x2 = target.x + target.w / 2, y2 = target.y;
    const delta = Math.max(28, Math.abs(y2 - y1) * 0.38);
    return `M ${x1} ${y1} C ${x1} ${y1 + delta}, ${x2} ${y2 - delta}, ${x2} ${y2}`;
  }

  edgeMidpoint(edge: CanvasEdge): { x: number; y: number } {
    const source = this.nodes().find(node => node.id === edge.from);
    const target = this.nodes().find(node => node.id === edge.to);
    return source && target
      ? { x: (source.x + source.w / 2 + target.x + target.w / 2) / 2, y: (source.y + source.h + target.y) / 2 }
      : { x: 0, y: 0 };
  }

  edgeMarkerSuffix(edge: CanvasEdge, selected: boolean): string {
    if (selected) return '-sel';
    return ({ on_success: '-ok', on_failure: '-fail', back_edge: '-loop' } as Record<string, string>)[edge.condition] ?? '';
  }

  forkPoints(node: CanvasNode): string {
    return `${node.w / 2},0 ${node.w},${node.h / 2} ${node.w / 2},${node.h} 0,${node.h / 2}`;
  }

  isComplexNode(node: CanvasNode): boolean {
    return ['task', 'det', 'gate', 'review', 'fork', 'join'].includes(node.type);
  }

  routingLabel(node: CanvasNode): string {
    const routing = node.routing;
    if (!routing || routing.mode === 'auto') return '';
    if (routing.mode === 'backend') return routing.backend ?? '';
    if (routing.mode === 'worker') return routing.workerName?.split('-').pop() ?? '';
    return routing.capability ?? '';
  }

  routingBadgeW(node: CanvasNode): number { return this.routingLabel(node).length * 5.5 + 10; }

  nodeLabel(nodeId: string): string {
    return this.nodes().find(node => node.id === nodeId)?.title ?? nodeId;
  }

  nodeIsActive(node: CanvasNode): boolean { return node.id === this.activeStepId; }
  runDetStep(node: CanvasNode): void { this.runDeterministic.emit(node); }

  private updateNode(id: string, update: (node: CanvasNode) => CanvasNode): void {
    this.nodes.update(nodes => nodes.map(node => node.id === id ? update(node) : node));
  }

  syncInputBindings(): void {
    const byInput = new Map<string, ArtifactBinding & { sourceId: string }>();
    for (const edge of this.edges()) {
      for (const binding of edge.bindings ?? []) byInput.set(`${edge.to}:${binding.inputName}`, { ...binding, sourceId: edge.from });
    }
    this.nodes.update(nodes => nodes.map(node => ({
      ...node,
      inputs: node.inputs.map(input => {
        const binding = byInput.get(`${node.id}:${input.name}`);
        return { ...input, producedByStepId: binding?.sourceId, producedByOutputName: binding?.outputName };
      }),
    })));
  }

  private defaultTitle(type: CanvasNode['type']): string {
    return ({
      task: 'Neuer Schritt', det: 'Deterministischer Schritt', gate: 'Verification Gate',
      review: 'Review / Freigabe', fork: 'Fork', join: 'Join',
    } as Partial<Record<CanvasNode['type'], string>>)[type] ?? type;
  }
}
