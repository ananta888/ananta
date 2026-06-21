import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  ViewChild,
} from '@angular/core';
import { ChSymbolReadModel } from '../models/codehug.models';

export interface ChGraphNode {
  id: string;
  label: string;
  kind: 'symbol' | 'file' | 'domain';
  /** Optional Sub-Label (z.B. Dateipfad, Typ). */
  meta?: string;
  /** Filter-Flag (vom Layer-Filter gesetzt). */
  hidden?: boolean;
}

export interface ChGraphEdge {
  from: string;
  to: string;
  /** 'caller' (from ruft to auf) oder 'callee' (from wird von to aufgerufen). */
  relation: 'caller' | 'callee' | 'reference';
}

export interface ChGraphFilter {
  kinds?: ChGraphNode['kind'][];
  domain?: string;
  searchText?: string;
  maxDepth?: number;
}

/**
 * CodeHugDependencyGraph — Eigenstaendiger CodeHug-Graph-Renderer fuer
 * Symbol/File/Domain-Beziehungen. SVG-basiert, KEIN Component-Reuse aus
 * features/codecompass-graph.
 *
 * Layout: einfaches Force-Directed-Layout (deterministisch, kein Random).
 * Features:
 * - Drag-and-Drop der Knoten (lokale Position wird persistiert)
 * - Filter (Knoten ausblenden)
 * - Suche
 * - Selection-Event
 * - Layout-State-Persistierung pro Graph
 *
 * SOLID: SRP — Darstellung + einfache Interaktion. Komplexere Operationen
 * (Annotationen, Grouping) folgen in spaeteren Iterationen.
 */
@Component({
  selector: 'ch-dependency-graph',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ch-dg">
      <div class="ch-dg-toolbar">
        <input
          type="search"
          class="ch-dg-search"
          placeholder="Suche…"
          [value]="filter.searchText ?? ''"
          (input)="onSearchChange($any($event.target).value)" />
        <label class="ch-dg-checkbox">
          <input type="checkbox" [checked]="kindActive('symbol')" (change)="onKindToggle('symbol', $any($event.target).checked)" />
          Symbole
        </label>
        <label class="ch-dg-checkbox">
          <input type="checkbox" [checked]="kindActive('file')" (change)="onKindToggle('file', $any($event.target).checked)" />
          Dateien
        </label>
        <label class="ch-dg-checkbox">
          <input type="checkbox" [checked]="kindActive('domain')" (change)="onKindToggle('domain', $any($event.target).checked)" />
          Domaenen
        </label>
        <button type="button" class="ch-dg-btn" (click)="resetLayout()">Layout zuruecksetzen</button>
      </div>
      <div #svgWrap class="ch-dg-canvas" data-testid="dep-graph-canvas"></div>
      <p class="ch-dg-status">
        Sichtbar: {{ visibleNodeCount() }} / {{ nodes.length }} Knoten,
        {{ visibleEdgeCount() }} / {{ edges.length }} Kanten
      </p>
    </div>
  `,
  styles: [`
    :host { display: block; height: 100%; }
    .ch-dg { display: flex; flex-direction: column; height: 100%; min-height: 320px; }
    .ch-dg-toolbar {
      display: flex;
      gap: 8px;
      align-items: center;
      padding: 6px 10px;
      border-bottom: 1px solid var(--border);
      background: var(--card-bg);
      font-size: 12px;
    }
    .ch-dg-search {
      padding: 4px 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--fg);
      font-size: 12px;
      min-width: 180px;
    }
    .ch-dg-checkbox { display: flex; align-items: center; gap: 4px; }
    .ch-dg-btn {
      padding: 4px 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 11px;
    }
    .ch-dg-canvas {
      flex: 1;
      overflow: hidden;
      background:
        radial-gradient(circle at 1px 1px, color-mix(in srgb, var(--border) 60%, transparent) 1px, transparent 0)
        0 0 / 20px 20px var(--bg);
    }
    .ch-dg-status {
      margin: 0;
      padding: 4px 10px;
      border-top: 1px solid var(--border);
      font-size: 11px;
      color: var(--muted);
      background: var(--card-bg);
    }
  `]
})
export class CodeHugDependencyGraphComponent implements AfterViewInit, OnChanges {
  @ViewChild('svgWrap', { static: true }) svgRef!: ElementRef<HTMLDivElement>;

  @Input() nodes: ChGraphNode[] = [];
  @Input() edges: ChGraphEdge[] = [];
  @Input() filter: ChGraphFilter = {};

  @Output() nodeSelected = new EventEmitter<string>();
  @Output() layoutChanged = new EventEmitter<Record<string, { x: number; y: number }>>();

  private positions = new Map<string, { x: number; y: number }>();
  private dragNodeId: string | null = null;
  private dragOffset = { x: 0, y: 0 };

  ngAfterViewInit(): void {
    this.layoutAndRender();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (this.svgRef) {
      this.layoutAndRender();
    }
  }

  onSearchChange(text: string): void {
    this.filter = { ...this.filter, searchText: text };
    this.layoutAndRender();
  }

  onKindToggle(kind: ChGraphNode['kind'], active: boolean): void {
    const current = this.filter.kinds ?? ['symbol', 'file', 'domain'];
    const next = active
      ? Array.from(new Set([...current, kind]))
      : current.filter(k => k !== kind);
    this.filter = { ...this.filter, kinds: next };
    this.layoutAndRender();
  }

  kindActive(kind: ChGraphNode['kind']): boolean {
    return (this.filter.kinds ?? ['symbol', 'file', 'domain']).includes(kind);
  }

  resetLayout(): void {
    this.positions.clear();
    this.layoutAndRender();
    this.layoutChanged.emit({});
  }

  visibleNodeCount(): number {
    return this.nodes.filter(n => this.isVisible(n)).length;
  }

  visibleEdgeCount(): number {
    const visibleNodes = new Set(this.nodes.filter(n => this.isVisible(n)).map(n => n.id));
    return this.edges.filter(e => visibleNodes.has(e.from) && visibleNodes.has(e.to)).length;
  }

  private isVisible(node: ChGraphNode): boolean {
    if (this.filter.kinds && !this.filter.kinds.includes(node.kind)) return false;
    if (this.filter.searchText) {
      const q = this.filter.searchText.toLowerCase();
      if (!node.label.toLowerCase().includes(q) && !(node.meta?.toLowerCase().includes(q))) return false;
    }
    if (this.filter.domain && node.meta && !node.meta.includes(this.filter.domain)) return false;
    return true;
  }

  /**
   * Einfaches deterministisches Layout: Kreisförmige Anordnung,
   * Connections beeinflussen die Position (Barycenter-step).
   */
  private layoutAndRender(): void {
    const visible = this.nodes.filter(n => this.isVisible(n));
    if (visible.length === 0) {
      this.svgRef.nativeElement.innerHTML = '';
      return;
    }

    // Initialize missing positions
    const width = this.svgRef.nativeElement.clientWidth || 600;
    const height = this.svgRef.nativeElement.clientHeight || 400;
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.min(width, height) * 0.35;
    visible.forEach((n, i) => {
      if (!this.positions.has(n.id)) {
        const angle = (i / Math.max(visible.length, 1)) * Math.PI * 2;
        this.positions.set(n.id, {
          x: cx + Math.cos(angle) * radius,
          y: cy + Math.sin(angle) * radius,
        });
      }
    });

    // Barycenter-Relaxation (10 iterations)
    const adjacency = this.buildAdjacency();
    for (let iter = 0; iter < 10; iter++) {
      for (const n of visible) {
        const neighbors = adjacency.get(n.id) ?? [];
        if (neighbors.length === 0) continue;
        let sx = 0, sy = 0, count = 0;
        for (const nb of neighbors) {
          const pos = this.positions.get(nb);
          if (pos) {
            sx += pos.x;
            sy += pos.y;
            count++;
          }
        }
        if (count > 0) {
          const cur = this.positions.get(n.id)!;
          const tx = sx / count;
          const ty = sy / count;
          this.positions.set(n.id, {
            x: cur.x + (tx - cur.x) * 0.3,
            y: cur.y + (ty - cur.y) * 0.3,
          });
        }
      }
    }

    this.renderSvg(visible);
  }

  private buildAdjacency(): Map<string, string[]> {
    const map = new Map<string, string[]>();
    for (const n of this.nodes) map.set(n.id, []);
    for (const e of this.edges) {
      if (!map.has(e.from)) map.set(e.from, []);
      if (!map.has(e.to)) map.set(e.to, []);
      map.get(e.from)!.push(e.to);
      map.get(e.to)!.push(e.from);
    }
    return map;
  }

  private renderSvg(visible: ChGraphNode[]): void {
    const host = this.svgRef.nativeElement;
    host.innerHTML = '';
    const width = host.clientWidth || 600;
    const height = host.clientHeight || 400;
    const svgNs = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNs, 'svg');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

    const visibleSet = new Set(visible.map(n => n.id));

    // Edges first
    const edgeGroup = document.createElementNS(svgNs, 'g');
    edgeGroup.setAttribute('stroke', '#94a3b8');
    edgeGroup.setAttribute('stroke-width', '1');
    for (const e of this.edges) {
      if (!visibleSet.has(e.from) || !visibleSet.has(e.to)) continue;
      const a = this.positions.get(e.from)!;
      const b = this.positions.get(e.to)!;
      const line = document.createElementNS(svgNs, 'line');
      line.setAttribute('x1', String(a.x));
      line.setAttribute('y1', String(a.y));
      line.setAttribute('x2', String(b.x));
      line.setAttribute('y2', String(b.y));
      if (e.relation === 'caller') line.setAttribute('stroke-dasharray', '4 2');
      edgeGroup.appendChild(line);
    }
    svg.appendChild(edgeGroup);

    // Nodes
    for (const n of visible) {
      const pos = this.positions.get(n.id)!;
      const g = document.createElementNS(svgNs, 'g');
      g.setAttribute('transform', `translate(${pos.x},${pos.y})`);
      g.setAttribute('data-node-id', n.id);
      g.setAttribute('style', 'cursor: pointer;');

      const r = n.kind === 'domain' ? 22 : n.kind === 'file' ? 16 : 14;
      const circle = document.createElementNS(svgNs, 'circle');
      circle.setAttribute('r', String(r));
      circle.setAttribute('fill', this.colorFor(n.kind));
      circle.setAttribute('stroke', '#475569');
      circle.setAttribute('stroke-width', '1');
      g.appendChild(circle);

      const text = document.createElementNS(svgNs, 'text');
      text.setAttribute('y', String(r + 12));
      text.setAttribute('text-anchor', 'middle');
      text.setAttribute('font-size', '10');
      text.setAttribute('fill', 'var(--fg, #111827)');
      text.textContent = this.shortLabel(n.label);
      g.appendChild(text);

      // Drag-Handlers
      g.addEventListener('mousedown', (ev: MouseEvent) => {
        ev.preventDefault();
        this.dragNodeId = n.id;
        this.dragOffset = { x: ev.offsetX - pos.x, y: ev.offsetY - pos.y };
        g.setAttribute('style', 'cursor: grabbing;');
      });

      g.addEventListener('click', () => {
        this.nodeSelected.emit(n.id);
      });

      svg.appendChild(g);
    }

    // Mouse-move auf SVG
    svg.addEventListener('mousemove', (ev: MouseEvent) => {
      if (!this.dragNodeId) return;
      const rect = svg.getBoundingClientRect();
      const newX = ev.clientX - rect.left - this.dragOffset.x;
      const newY = ev.clientY - rect.top - this.dragOffset.y;
      this.positions.set(this.dragNodeId, { x: newX, y: newY });
      const draggedEl = svg.querySelector(`[data-node-id="${this.dragNodeId}"]`);
      if (draggedEl) draggedEl.setAttribute('transform', `translate(${newX},${newY})`);
      // Re-render edges
      const newA = this.positions.get(this.dragNodeId)!;
      edgeGroup.innerHTML = '';
      for (const e of this.edges) {
        if (!visibleSet.has(e.from) || !visibleSet.has(e.to)) continue;
        const a = this.positions.get(e.from);
        const b = this.positions.get(e.to);
        if (!a || !b) continue;
        const line = document.createElementNS(svgNs, 'line');
        line.setAttribute('x1', String(a.x));
        line.setAttribute('y1', String(a.y));
        line.setAttribute('x2', String(b.x));
        line.setAttribute('y2', String(b.y));
        if (e.relation === 'caller') line.setAttribute('stroke-dasharray', '4 2');
        edgeGroup.appendChild(line);
      }
    });
    svg.addEventListener('mouseup', () => {
      if (this.dragNodeId) {
        const draggedEl = svg.querySelector(`[data-node-id="${this.dragNodeId}"]`);
        if (draggedEl) draggedEl.setAttribute('style', 'cursor: pointer;');
        this.dragNodeId = null;
        this.layoutChanged.emit(Object.fromEntries(this.positions));
      }
    });

    host.appendChild(svg);
  }

  private colorFor(kind: ChGraphNode['kind']): string {
    if (kind === 'symbol') return '#bfdbfe'; // hellblau
    if (kind === 'file') return '#bbf7d0';   // hellgruen
    if (kind === 'domain') return '#fef3c7'; // hellgelb
    return '#e5e7eb';
  }

  private shortLabel(label: string): string {
    return label.length > 22 ? label.slice(0, 20) + '…' : label;
  }
}