import { ComponentFixture, TestBed } from '@angular/core/testing';
import type { ForceGraph3DInstance } from '3d-force-graph';
import { describe, expect, it, vi } from 'vitest';

import { RenderGraph } from '../models/render-graph.models';
import {
  FORCE_GRAPH_3D_FACTORY,
  ForceGraph3dFactoryPort,
} from '../ports/force-graph-3d-factory.port';
import {
  ForceGraph3dRendererComponent,
  renderGraphTooltipElement,
} from './force-graph-3d-renderer.component';

const GRAPH: RenderGraph = {
  nodes: [
    { id: 'a', label: 'A', kind: 'team', tooltip: 'Team A' },
    { id: 'b', label: 'B', kind: 'role', tooltip: 'Role B' },
  ],
  edges: [
    { id: 'ab', sourceId: 'a', targetId: 'b', kind: 'contains', label: 'contains', tooltip: 'A contains B' },
  ],
};

describe('ForceGraph3dRendererComponent', () => {
  it('uses the renderer factory and forwards graph selection without owning domain objects', async () => {
    const fake = forceGraphFake();
    const factory: ForceGraph3dFactoryPort = {
      webglAvailable: () => true,
      create: vi.fn(async () => fake.renderer),
    };
    const fixture = await createFixture(factory);
    let selected = '';
    fixture.componentInstance.nodeSelected.subscribe(id => selected = id);

    fixture.componentRef.setInput('graph', GRAPH);
    fixture.detectChanges();

    await vi.waitFor(() => expect(fixture.componentInstance.loading).toBe(false));
    expect(fixture.componentInstance.error).toBe('');
    expect(fake.calls.has('graphData')).toBe(true);
    expect(factory.create).toHaveBeenCalledOnce();
    expect(fake.calls.get('graphData')).toHaveBeenCalledWith({
      nodes: [
        { id: 'a', label: 'A', kind: 'team' },
        { id: 'b', label: 'B', kind: 'role' },
      ],
      links: [{ id: 'ab', source: 'a', target: 'b', label: 'contains' }],
    });

    fake.nodeClick?.({ id: 'a' });
    expect(selected).toBe('a');
    fixture.destroy();
    expect(fake.destructor).toHaveBeenCalledOnce();
  });

  it('rejects an oversized graph explicitly and never constructs WebGL', async () => {
    const factory: ForceGraph3dFactoryPort = {
      webglAvailable: () => true,
      create: vi.fn(),
    };
    const fixture = await createFixture(factory);
    fixture.componentRef.setInput('graph', GRAPH);
    fixture.componentRef.setInput('nodeRenderLimit', 1);
    fixture.componentRef.setInput('limitStrategy', 'reject');
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('3D render limit exceeded');
    expect(fixture.nativeElement.textContent).toContain('2 nodes / 1 edges');
    expect(factory.create).not.toHaveBeenCalled();
  });

  it('announces a WebGL fallback and emits unavailable', async () => {
    const factory: ForceGraph3dFactoryPort = {
      webglAvailable: () => false,
      create: vi.fn(),
    };
    const fixture = await createFixture(factory);
    let available: boolean | null = null;
    fixture.componentInstance.availabilityChange.subscribe(value => available = value);
    fixture.componentRef.setInput('graph', GRAPH);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('WebGL is not available');
    expect(available).toBe(false);
    expect(factory.create).not.toHaveBeenCalled();
  });

  it('uses an explained static fallback instead of a moving graph for reduced motion', async () => {
    const originalMatchMedia = globalThis.matchMedia;
    Object.defineProperty(globalThis, 'matchMedia', {
      configurable: true,
      value: vi.fn(() => ({ matches: true })),
    });
    const factory: ForceGraph3dFactoryPort = {
      webglAvailable: () => true,
      create: vi.fn(),
    };
    try {
      const fixture = await createFixture(factory);
      let available: boolean | null = null;
      fixture.componentInstance.availabilityChange.subscribe(value => available = value);
      fixture.componentRef.setInput('graph', GRAPH);
      fixture.detectChanges();

      expect(fixture.nativeElement.textContent).toContain('Reduced motion is enabled');
      expect(fixture.nativeElement.textContent).toContain('synchronized static list');
      expect(available).toBe(false);
      expect(factory.create).not.toHaveBeenCalled();
      fixture.destroy();
    } finally {
      Object.defineProperty(globalThis, 'matchMedia', {
        configurable: true,
        value: originalMatchMedia,
      });
    }
  });

  it('creates tooltip DOM through textContent', () => {
    const tooltip = renderGraphTooltipElement('<img src=x onerror=alert(1)>');
    expect(tooltip.querySelector('img')).toBeNull();
    expect(tooltip.textContent).toBe('<img src=x onerror=alert(1)>');
  });

  it('refreshes styles and tooltips without rebuilding an unchanged topology', async () => {
    const fake = forceGraphFake();
    const factory: ForceGraph3dFactoryPort = {
      webglAvailable: () => true,
      create: vi.fn(async () => fake.renderer),
    };
    const fixture = await createFixture(factory);
    fixture.componentRef.setInput('graph', GRAPH);
    fixture.detectChanges();
    await vi.waitFor(() => expect(fixture.componentInstance.loading).toBe(false));
    const refresh = fake.calls.get('refresh')!;
    const refreshCount = refresh.mock.calls.length;

    fixture.componentRef.setInput('graph', {
      nodes: GRAPH.nodes.map(node => ({ ...node, tooltip: `${node.tooltip} updated` })),
      edges: GRAPH.edges.map(edge => ({ ...edge, tooltip: `${edge.tooltip} updated` })),
    });
    fixture.componentRef.setInput('nodeStyles', { a: { color: '#FF0000', size: 12 } });
    fixture.detectChanges();

    expect(factory.create).toHaveBeenCalledOnce();
    expect(fake.destructor).not.toHaveBeenCalled();
    expect(refresh.mock.calls.length).toBeGreaterThan(refreshCount);
  });

  it('keeps selection controlled by inputs and emits explicit clear intent', async () => {
    const fake = forceGraphFake();
    const factory: ForceGraph3dFactoryPort = {
      webglAvailable: () => true,
      create: vi.fn(async () => fake.renderer),
    };
    const fixture = await createFixture(factory);
    const selected = vi.fn();
    const cleared = vi.fn();
    fixture.componentInstance.nodeSelected.subscribe(selected);
    fixture.componentInstance.selectionCleared.subscribe(cleared);
    fixture.componentRef.setInput('graph', GRAPH);
    fixture.componentRef.setInput('selectedNodeId', 'a');
    fixture.detectChanges();
    await vi.waitFor(() => expect(fixture.componentInstance.loading).toBe(false));

    const nodeColor = fake.calls.get('nodeColor')!.mock.calls.at(-1)![0] as (node: { id: string }) => string;
    expect(nodeColor({ id: 'a' })).toBe('#f59e0b');

    fake.nodeClick?.({ id: 'a' });
    expect(selected).toHaveBeenCalledWith('a');
    expect(nodeColor({ id: 'a' })).toBe('#f59e0b');

    fake.backgroundClick?.();
    expect(cleared).toHaveBeenCalledOnce();
    expect(nodeColor({ id: 'a' })).toBe('#f59e0b');

    fixture.componentRef.setInput('selectedNodeId', null);
    fixture.detectChanges();
    expect(nodeColor({ id: 'a' })).toBe('#64748b');

    fixture.componentRef.setInput('selectedNodeId', 'outside-visible-scope');
    fixture.detectChanges();
    expect(nodeColor({ id: 'a' })).toBe('#64748b');
  });
});

async function createFixture(factory: ForceGraph3dFactoryPort): Promise<ComponentFixture<ForceGraph3dRendererComponent>> {
  await TestBed.configureTestingModule({
    imports: [ForceGraph3dRendererComponent],
    providers: [{ provide: FORCE_GRAPH_3D_FACTORY, useValue: factory }],
  }).compileComponents();
  const fixture = TestBed.createComponent(ForceGraph3dRendererComponent);
  fixture.detectChanges();
  return fixture;
}

function forceGraphFake(): {
  renderer: ForceGraph3DInstance;
  calls: Map<string, ReturnType<typeof vi.fn>>;
  destructor: ReturnType<typeof vi.fn>;
  nodeClick: ((node: { id: string }) => void) | null;
  backgroundClick: (() => void) | null;
} {
  const calls = new Map<string, ReturnType<typeof vi.fn>>();
  const destructor = vi.fn();
  const state: {
    nodeClick: ((node: { id: string }) => void) | null;
    backgroundClick: (() => void) | null;
  } = { nodeClick: null, backgroundClick: null };
  let proxy: ForceGraph3DInstance;
  proxy = new Proxy({}, {
    get: (_target, property) => {
      if (property === 'then') return undefined;
      if (property === '_destructor') return destructor;
      if (property === 'd3Force') return () => ({ strength: vi.fn(), distance: vi.fn() });
      const name = String(property);
      if (!calls.has(name)) {
        calls.set(name, vi.fn((...args: unknown[]) => {
          if (name === 'onNodeClick') state.nodeClick = args[0] as (node: { id: string }) => void;
          if (name === 'onBackgroundClick') state.backgroundClick = args[0] as () => void;
          return proxy;
        }));
      }
      return calls.get(name);
    },
  }) as ForceGraph3DInstance;
  return {
    renderer: proxy,
    calls,
    destructor,
    get nodeClick() { return state.nodeClick; },
    get backgroundClick() { return state.backgroundClick; },
  };
}
