import { vi, describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';

import { CodeHugCanvasComponent } from './codehug-canvas.component';
import { ChTopologyReadModel } from '../models/codehug.models';

beforeAll(async () => {
  await ɵresolveComponentResources(resource =>
    readFile(new URL(resource, import.meta.url), 'utf8'),
  );
});

function makeTopology(): ChTopologyReadModel {
  return {
    snapshot_id: 'snap-1',
    generated_at: 1700000000,
    hubs: [
      { id: 'hub-a', url: 'http://hub-a.local:8200', status: 'online', version: '1.0.0', startedAt: 1700000000 } as any,
    ],
    workers: [],
    agents: [],
    routing_rules: [],
    test_layers: [],
    automation_runs: [],
    agent_steps: [],
    connections: [],
    routingRules: [],
    activeLayers: [],
  } as unknown as ChTopologyReadModel;
}

describe('CodeHugCanvasComponent (FSR-T042 acceptance)', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CodeHugCanvasComponent],
    }).compileComponents();
  });

  it('mounts with empty nodes and edges when no topology is provided', () => {
    const fixture = TestBed.createComponent(CodeHugCanvasComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    expect(component.nodes()).toEqual([]);
    expect(component.edges()).toEqual([]);
    expect(component.selectedNodeId()).toBeNull();
  });

  it('uses default pan and zoom values out of the box', () => {
    const fixture = TestBed.createComponent(CodeHugCanvasComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    expect(component.panX()).toBe(40);
    expect(component.panY()).toBe(30);
    expect(component.zoom()).toBe(1);
  });

  it('produces canvas nodes from a non-empty topology via ngOnChanges', () => {
    const fixture = TestBed.createComponent(CodeHugCanvasComponent);
    const component = fixture.componentInstance;
    component.topology = makeTopology();
    component.ngOnChanges({
      topology: {
        currentValue: component.topology,
        previousValue: null,
        firstChange: true,
        isFirstChange: () => true,
      } as any,
    });
    expect(component.nodes().length).toBeGreaterThan(0);
  });

  it('zoomIn() increases the zoom signal up to its clamp limit', () => {
    const fixture = TestBed.createComponent(CodeHugCanvasComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const before = component.zoom();
    component.zoomIn();
    expect(component.zoom()).toBeGreaterThan(before);
    expect(component.zoom()).toBeLessThanOrEqual(2.5);
  });

  it('zoomOut() decreases the zoom signal down to its clamp limit', () => {
    const fixture = TestBed.createComponent(CodeHugCanvasComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const before = component.zoom();
    component.zoomOut();
    expect(component.zoom()).toBeLessThan(before);
    expect(component.zoom()).toBeGreaterThanOrEqual(0.2);
  });

  it('resetView() restores default pan and zoom', () => {
    const fixture = TestBed.createComponent(CodeHugCanvasComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.panX.set(300);
    component.panY.set(200);
    component.zoom.set(2);
    component.resetView();
    expect(component.panX()).toBe(40);
    expect(component.panY()).toBe(30);
    expect(component.zoom()).toBe(1);
  });

  it('clearSelection() resets selectedNodeId to null', () => {
    const fixture = TestBed.createComponent(CodeHugCanvasComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.selectedNodeId.set('hub-a');
    component.clearSelection();
    expect(component.selectedNodeId()).toBeNull();
  });

  it('emits refreshRequested when an internal refresh is requested', () => {
    const fixture = TestBed.createComponent(CodeHugCanvasComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const spy = vi.fn();
    component.refreshRequested.subscribe(spy);
    component.refreshRequested.emit();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('renders an svg element after init', () => {
    const fixture = TestBed.createComponent(CodeHugCanvasComponent);
    fixture.detectChanges();
    const html = fixture.nativeElement as HTMLElement;
    const svg = html.querySelector('svg');
    expect(svg).not.toBeNull();
  });
});