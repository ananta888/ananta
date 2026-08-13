import { ɵresolveComponentResources } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import type { VpEdge, VpGraph, VpStep } from '../../visual-process/visual-process-api.service';
import {
  CaseFlowAgentCanvasComponent,
  moveCaseFlowAgentNode,
} from './caseflow-agent-canvas.component';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('CaseFlowAgentCanvasComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CaseFlowAgentCanvasComponent],
    }).compileComponents();
  });

  it('renders a VpGraph directly and exposes keyboard-selectable nodes and edges', () => {
    const fixture = createFixture();
    const component = fixture.componentInstance;
    const selectedNodes: string[] = [];
    const selectedEdges: string[] = [];
    component.nodeSelected.subscribe(id => selectedNodes.push(id));
    component.edgeSelected.subscribe(id => selectedEdges.push(id));

    const canvas = fixture.nativeElement.querySelector('[role="application"]') as SVGElement;
    const node = fixture.nativeElement.querySelector('[data-step-id="agent-a"]') as SVGGElement;
    const edge = fixture.nativeElement.querySelector('[data-edge-id="edge-ab"]') as SVGGElement;

    expect(canvas.getAttribute('aria-label')).toContain('Drei Agenten');
    expect(fixture.nativeElement.querySelectorAll('.agent-node')).toHaveLength(3);
    expect(fixture.nativeElement.querySelector('app-visual-process-editor')).toBeNull();

    node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    edge.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));

    expect(selectedNodes).toEqual(['agent-a']);
    expect(selectedEdges).toEqual(['edge-ab']);
    expect(node.getAttribute('role')).toBe('button');
    expect(edge.getAttribute('aria-label')).toContain('agent-a nach agent-b');
  });

  it('emits one focused immutable graph change when exactly one node is dragged', () => {
    const fixture = createFixture();
    const component = fixture.componentInstance;
    const changes: VpGraph[] = [];
    component.graphChange.subscribe(graph => changes.push(graph));
    const node = fixture.nativeElement.querySelector('[data-step-id="agent-b"]') as SVGGElement;

    node.dispatchEvent(domPointerEvent('pointerdown', 9, 100, 120));
    document.dispatchEvent(domPointerEvent('pointermove', 9, 148, 144));
    document.dispatchEvent(domPointerEvent('pointerup', 9, 148, 144));

    expect(changes).toHaveLength(1);
    const updated = changes[0];
    expect(updated).not.toBe(AGENT_GRAPH);
    expect(updated.steps).not.toBe(AGENT_GRAPH.steps);
    expect(updated.edges).toBe(AGENT_GRAPH.edges);
    expect(updated.extensions).toBe(AGENT_GRAPH.extensions);
    expect(updated.steps[0]).toBe(AGENT_GRAPH.steps[0]);
    expect(updated.steps[2]).toBe(AGENT_GRAPH.steps[2]);
    expect(updated.steps[1]).not.toBe(AGENT_GRAPH.steps[1]);
    expect(updated.steps[1]).toEqual({
      ...AGENT_GRAPH.steps[1],
      position: { x: 348, y: 24 },
    });
    expect(updated.steps[1].io).toBe(AGENT_GRAPH.steps[1].io);
    expect(updated.steps[1].metadata).toBe(AGENT_GRAPH.steps[1].metadata);
  });

  it('supports zoom, pan, fit-to-view, viewport reset, and layout reset for three nodes', () => {
    const fixture = createFixture();
    const component = fixture.componentInstance;
    vi.spyOn(component.surfaceRef.nativeElement, 'getBoundingClientRect').mockReturnValue(rect(900, 600));

    component.zoomIn();
    expect(component.viewport.scale).toBeCloseTo(1.2);
    component.zoomOut();
    expect(component.viewport.scale).toBeCloseTo(1);

    component.startCanvasPan(pointerEvent(3, 10, 20));
    component.onPointerMove(pointerEvent(3, 55, 90));
    component.finishPointerInteraction(pointerEvent(3, 55, 90));
    expect(component.viewport).toEqual({ x: 45, y: 70, scale: 1 });

    component.fitToView();
    expect(component.viewport.scale).toBeGreaterThan(1);
    expect(component.viewport).not.toEqual({ x: 45, y: 70, scale: 1 });
    component.resetViewport();
    expect(component.viewport).toEqual({ x: 0, y: 0, scale: 1 });

    const movedGraph = moveCaseFlowAgentNode(AGENT_GRAPH, 'agent-c', { x: 610, y: 410 });
    fixture.componentRef.setInput('graph', movedGraph);
    fixture.detectChanges();
    const layoutChanges: VpGraph[] = [];
    component.graphChange.subscribe(graph => layoutChanges.push(graph));
    component.resetLayout();

    expect(layoutChanges).toHaveLength(1);
    expect(layoutChanges[0].steps.find(step => step.id === 'agent-c')!.position).toEqual({ x: 150, y: 220 });
    expect(layoutChanges[0].steps.find(step => step.id === 'agent-a')).toBe(movedGraph.steps[0]);
    expect(layoutChanges[0].steps.find(step => step.id === 'agent-b')).toBe(movedGraph.steps[1]);
  });

  it('preserves selection, viewport, and positions across an immutable update while the id remains', () => {
    const fixture = createFixture();
    const component = fixture.componentInstance;
    const node = fixture.nativeElement.querySelector('[data-step-id="agent-b"]') as SVGGElement;
    node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    component.zoomIn();
    component.startCanvasPan(pointerEvent(4, 0, 0));
    component.onPointerMove(pointerEvent(4, 31, 47));
    component.finishPointerInteraction(pointerEvent(4, 31, 47));
    const viewportBefore = component.viewport;
    const transformsBefore = nodeTransforms(fixture);

    const immutableUpdate: VpGraph = {
      ...AGENT_GRAPH,
      name: 'Drei Agenten, aktualisiert',
      steps: AGENT_GRAPH.steps.map(step => step.id === 'agent-a'
        ? { ...step, label: 'Agent A neu' }
        : step),
    };
    fixture.componentRef.setInput('graph', immutableUpdate);
    fixture.detectChanges();

    expect(component.selectedId).toBe('agent-b');
    expect(component.viewport).toEqual(viewportBefore);
    expect(nodeTransforms(fixture)).toEqual(transformsBefore);
    expect(fixture.nativeElement.querySelector('[data-step-id="agent-b"]')?.getAttribute('aria-pressed')).toBe('true');
  });

  it('places the arrow marker only at the target of a directed edge', () => {
    const fixture = createFixture();
    const line = fixture.nativeElement.querySelector(
      '[data-edge-id="edge-bc"] .edge-line',
    ) as SVGPathElement;

    expect(line.getAttribute('marker-end')).toContain('caseflow-agent-arrow-');
    expect(line.hasAttribute('marker-start')).toBe(false);
    expect(line.getAttribute('d')).toMatch(/^M .* L /);
  });

  it('renders canonical reverse edges as two visually distinct explicit directions', () => {
    const fixture = createFixture();
    const forward = fixture.nativeElement.querySelector('[data-edge-id="edge-ab"]') as SVGGElement;
    const reverse = fixture.nativeElement.querySelector('[data-edge-id="edge-ba"]') as SVGGElement;
    const forwardPath = forward.querySelector('.edge-line')!.getAttribute('d');
    const reversePath = reverse.querySelector('.edge-line')!.getAttribute('d');

    expect(forward.getAttribute('data-direction')).toBe('agent-a->agent-b');
    expect(reverse.getAttribute('data-direction')).toBe('agent-b->agent-a');
    expect(forward.getAttribute('data-reverse-edge-ids')).toBe('edge-ba');
    expect(reverse.getAttribute('data-reverse-edge-ids')).toBe('edge-ab');
    expect(forward.classList).toContain('bidirectional-edge');
    expect(reverse.classList).toContain('bidirectional-edge');
    expect(forwardPath).toContain(' Q ');
    expect(reversePath).toContain(' Q ');
    expect(forwardPath).not.toBe(reversePath);
  });

  it('emits exactly the canonical edge id of the clicked direction', () => {
    const fixture = createFixture();
    const selected: string[] = [];
    fixture.componentInstance.edgeSelected.subscribe(id => selected.push(id));

    const reverse = fixture.nativeElement.querySelector('[data-edge-id="edge-ba"]') as SVGGElement;
    reverse.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(selected).toEqual(['edge-ba']);
    expect(fixture.componentInstance.selectedId).toBe('edge-ba');
  });

  it('renders a loop with its own cubic geometry and unchanged canonical edge id', () => {
    const fixture = createFixture();
    const loop = fixture.nativeElement.querySelector('[data-edge-id="edge-cc"]') as SVGGElement;
    const line = loop.querySelector('.edge-line') as SVGPathElement;

    expect(loop.classList).toContain('loop-edge');
    expect(loop.getAttribute('data-loop')).toBe('true');
    expect(loop.getAttribute('data-edge-id')).toBe('edge-cc');
    expect(line.getAttribute('d')).toMatch(/^M .* C /);
    expect(line.getAttribute('marker-end')).toContain('caseflow-agent-arrow-');
  });

  it('renders a non-self back edge as directed feedback ending at its target', () => {
    const fixture = createFixture();
    const feedback = fixture.nativeElement.querySelector('[data-edge-id="edge-ba"]') as SVGGElement;
    const line = feedback.querySelector('.edge-line') as SVGPathElement;

    expect(feedback.classList).toContain('feedback-edge');
    expect(feedback.classList).not.toContain('loop-edge');
    expect(feedback.getAttribute('data-feedback')).toBe('true');
    expect(feedback.getAttribute('data-loop')).toBe('false');
    expect(feedback.getAttribute('data-direction')).toBe('agent-b->agent-a');
    expect(line.getAttribute('d')).toMatch(/^M .* Q .* /);
    expect(line.getAttribute('marker-end')).toContain('caseflow-agent-arrow-');
  });

  it('moves only the focused node with arrow keys and exposes button pressed state', () => {
    const fixture = createFixture();
    const changes: VpGraph[] = [];
    fixture.componentInstance.graphChange.subscribe(graph => changes.push(graph));
    const node = fixture.nativeElement.querySelector('[data-step-id="agent-b"]') as SVGGElement;
    const event = new KeyboardEvent('keydown', {
      key: 'ArrowRight',
      bubbles: true,
      cancelable: true,
      shiftKey: true,
    });

    node.dispatchEvent(event);
    fixture.detectChanges();

    expect(event.defaultPrevented).toBe(true);
    expect(node.getAttribute('aria-pressed')).toBe('true');
    expect(changes).toHaveLength(1);
    expect(changes[0].steps[1].position).toEqual({ x: 350, y: 0 });
    expect(changes[0].steps[0]).toBe(AGENT_GRAPH.steps[0]);
    expect(changes[0].steps[2]).toBe(AGENT_GRAPH.steps[2]);
    expect(changes[0].edges).toBe(AGENT_GRAPH.edges);
    expect(changes[0].extensions).toBe(AGENT_GRAPH.extensions);
  });
});

function createFixture(graph = AGENT_GRAPH): ComponentFixture<CaseFlowAgentCanvasComponent> {
  const fixture = TestBed.createComponent(CaseFlowAgentCanvasComponent);
  fixture.componentRef.setInput('graph', graph);
  fixture.detectChanges();
  return fixture;
}

function nodeTransforms(fixture: ComponentFixture<CaseFlowAgentCanvasComponent>): string[] {
  return Array.from(
    fixture.nativeElement.querySelectorAll('.agent-node') as NodeListOf<SVGGElement>,
    node => node.getAttribute('transform') ?? '',
  );
}

function pointerEvent(pointerId: number, clientX: number, clientY: number): PointerEvent {
  return {
    button: 0,
    pointerId,
    clientX,
    clientY,
    currentTarget: null,
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
  } as unknown as PointerEvent;
}

function domPointerEvent(
  type: 'pointerdown' | 'pointermove' | 'pointerup',
  pointerId: number,
  clientX: number,
  clientY: number,
): PointerEvent {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    button: 0,
    clientX,
    clientY,
  });
  Object.defineProperty(event, 'pointerId', { value: pointerId });
  return event as PointerEvent;
}

function rect(width: number, height: number): DOMRect {
  return {
    x: 0,
    y: 0,
    top: 0,
    right: width,
    bottom: height,
    left: 0,
    width,
    height,
    toJSON: () => ({}),
  } as DOMRect;
}

const AGENT_GRAPH: VpGraph = {
  id: 'caseflow-agents',
  name: 'Drei Agenten',
  description: 'Canvas component fixture',
  version: '1.0.0',
  steps: [
    agentStep('agent-a', 'Agent A', 'architect', 0, 0),
    agentStep('agent-b', 'Agent B', 'developer', 300, 0),
    agentStep('agent-c', 'Agent C', 'reviewer', 150, 220),
  ],
  edges: [
    agentEdge('edge-ab', 'agent-a', 'agent-b'),
    agentEdge('edge-ba', 'agent-b', 'agent-a', undefined, 'back_edge'),
    agentEdge('edge-bc', 'agent-b', 'agent-c'),
    agentEdge('edge-cc', 'agent-c', 'agent-c', 'review loop', 'back_edge'),
  ],
  tags: ['caseflow'],
  metadata: { preserve: true },
  extensions: { future_extension: { preserve: true } },
};

function agentStep(id: string, label: string, role: string, x: number, y: number): VpStep {
  return {
    id,
    label,
    role,
    kind: 'agent',
    io: { inputs: [], outputs: [] },
    position: { x, y },
    policy_hints: [],
    gate: false,
    metadata: { preserve: { id } },
  };
}

function agentEdge(
  id: string,
  source: string,
  target: string,
  label?: string,
  conditionKind: 'always' | 'back_edge' = 'always',
): VpEdge {
  return {
    id,
    source,
    target,
    condition: conditionKind === 'back_edge'
      ? { kind: 'back_edge', loop_policy: { kind: 'fixed', max_iterations: 1 } }
      : { kind: 'always' },
    ...(label ? { label } : {}),
  };
}
