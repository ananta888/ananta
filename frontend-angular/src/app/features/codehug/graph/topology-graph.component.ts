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
  signal,
} from '@angular/core';
import { ChWorkerInstanceReadModel, ChHubInstanceReadModel, ChTopologyConnection } from '../models/codehug.models';

/**
 * TopologyGraphComponent — minimaler SVG-Fallback für Hub/Worker-Topologie.
 *
 * Ersetzt durch CodeHugCanvasComponent in der Internals-Ansicht.
 * Diese Komponente bleibt als Fallback für Kontexte erhalten, die nur
 * eine einfache, schreibgeschützte Topologie-Übersicht benötigen.
 */
@Component({
  selector: 'ch-topology-graph',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ch-topology-graph">
      <div #canvas class="ch-topology-canvas" data-testid="topology-canvas"></div>
      @if (loading()) {
        <p class="ch-topology-status">Topologie wird geladen…</p>
      } @else if (hubs.length === 0 && workers.length === 0) {
        <p class="ch-topology-status">Keine Hub- oder Worker-Instanzen registriert.</p>
      }
    </div>
  `,
  styles: [`
    :host { display: block; height: 100%; }
    .ch-topology-graph { position: relative; height: 100%; min-height: 320px; }
    .ch-topology-canvas { width: 100%; height: 100%; min-height: 320px; }
    .ch-topology-status {
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      color: var(--muted);
      font-size: 13px;
      margin: 0;
    }
  `]
})
export class TopologyGraphComponent implements AfterViewInit, OnChanges {
  @ViewChild('canvas', { static: true }) canvasRef!: ElementRef<HTMLDivElement>;

  @Input() hubs: ChHubInstanceReadModel[] = [];
  @Input() workers: ChWorkerInstanceReadModel[] = [];
  @Input() connections: ChTopologyConnection[] = [];
  @Input() selectedWorkerId: string | null = null;

  @Output() workerSelected = new EventEmitter<string>();

  readonly loading = signal(true);

  ngAfterViewInit(): void {
    this.renderSvg();
    this.loading.set(false);
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (this.canvasRef && (changes['hubs'] || changes['workers'] || changes['selectedWorkerId'])) {
      this.renderSvg();
    }
  }

  private renderSvg(): void {
    const host = this.canvasRef.nativeElement;
    host.innerHTML = '';
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '320');
    svg.setAttribute('viewBox', '0 0 600 320');

    let hubX = 250;
    this.hubs.forEach((h, i) => {
      svg.appendChild(this.makeNode(h.id, hubX + i * 80, 20, 100, 40, 'hub', h.url));
    });

    this.workers.forEach((w, i) => {
      const y = 120 + Math.floor(i / 4) * 80;
      const x = 30 + (i % 4) * 140;
      const node = this.makeNode(w.id, x, y, 120, 50, w.cliBackend === 'deterministic' ? 'det' : 'llm', `${w.cliBackend} · ${w.model}`);
      if (this.selectedWorkerId === w.id) {
        node.querySelector('rect')?.setAttribute('stroke', '#7c3aed');
        node.querySelector('rect')?.setAttribute('stroke-width', '2.5');
      }
      node.addEventListener('click', () => this.workerSelected.emit(w.id));
      svg.appendChild(node);
    });

    host.appendChild(svg);
  }

  private makeNode(id: string, x: number, y: number, w: number, h: number, kind: string, label: string): SVGGElement {
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('data-id', id);
    g.setAttribute('transform', `translate(${x},${y})`);
    g.style.cursor = 'pointer';

    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('width', String(w));
    rect.setAttribute('height', String(h));
    rect.setAttribute('rx', '8');
    rect.setAttribute('fill', kind === 'det' ? '#e5e7eb' : kind === 'hub' ? '#fef3c7' : '#dbeafe');
    rect.setAttribute('stroke', '#6b7280');
    rect.setAttribute('stroke-width', '1.5');
    g.appendChild(rect);

    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', String(w / 2));
    text.setAttribute('y', String(h / 2 - 4));
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('font-size', '11');
    text.setAttribute('font-weight', '600');
    text.setAttribute('fill', '#111827');
    text.textContent = id.length > 16 ? id.slice(0, 14) + '…' : id;
    g.appendChild(text);

    const sub = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    sub.setAttribute('x', String(w / 2));
    sub.setAttribute('y', String(h / 2 + 12));
    sub.setAttribute('text-anchor', 'middle');
    sub.setAttribute('font-size', '9');
    sub.setAttribute('fill', '#6b7280');
    sub.textContent = label.length > 20 ? label.slice(0, 18) + '…' : label;
    g.appendChild(sub);

    return g;
  }
}
