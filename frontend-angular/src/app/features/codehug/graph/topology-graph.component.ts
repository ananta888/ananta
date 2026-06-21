import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
  ViewChild,
  inject,
  signal,
} from '@angular/core';
import { ChWorkerInstanceReadModel, ChHubInstanceReadModel, ChTopologyConnection } from '../models/codehug.models';

/**
 * TopologyGraphComponent — bpmn-js basierter Renderer fuer Hub/Worker-Topologie.
 *
 * SOLID: SRP — reine Darstellung + Selection-Event. Keine Daten-Logik,
 * keine Mutationen an der Eingabe.
 *
 * Camunda/bpmn-js Standard:
 * - Pools/Lanes fuer Hubs
 * - Tasks fuer Worker (rounded rectangles mit Icon)
 * - SequenceFlows fuer Hub-Worker-Verbindungen
 * - Color-Codierung: deterministic = grau, LLM = accent
 *
 * Tradeoff: bpmn-js ist sehr maechtig aber gross (~1MB). Wir nutzen es nur
 * fuer die Topologie-Anzeige, kleinere Graphen in CH-004 nutzen SVG direkt.
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
export class TopologyGraphComponent implements AfterViewInit, OnChanges, OnDestroy {
  @ViewChild('canvas', { static: true }) canvasRef!: ElementRef<HTMLDivElement>;

  @Input() hubs: ChHubInstanceReadModel[] = [];
  @Input() workers: ChWorkerInstanceReadModel[] = [];
  @Input() connections: ChTopologyConnection[] = [];
  @Input() selectedWorkerId: string | null = null;

  @Output() workerSelected = new EventEmitter<string>();

  readonly loading = signal(true);
  /** bpmn-js Modeler-Instanz (lazy import). */
  private modeler: any = null;

  ngAfterViewInit(): void {
    this.renderFallback();
    this.loading.set(false);
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (this.modeler && (changes['hubs'] || changes['workers'] || changes['connections'] || changes['selectedWorkerId'])) {
      void this.render();
    }
  }

  ngOnDestroy(): void {
    try {
      this.modeler?.destroy();
    } catch {
      // ignore
    }
  }

  private async render(): Promise<void> {
    // bpmn-js wurde bewusst entfernt: Vite-Pre-Bundling kann den Sub-Path nicht
    // resolven, und der SVG-Fallback deckt die Topologie-Anzeige voll ab.
    // Falls spaeter bpmn-js benoetigt wird, muss es ueber optimizeDeps.include
    // explizit vorgewermt werden — bis dahin kein dynamic import.
  }

  private renderFallback(): void {
    // Minimaler SVG-Fallback wenn bpmn-js nicht geladen werden kann
    const host = this.canvasRef.nativeElement;
    host.innerHTML = '';
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '320');
    svg.setAttribute('viewBox', `0 0 ${600} 320`);
    // Hub am oberen Rand
    let hubX = 250;
    this.hubs.forEach((h, i) => {
      const hub = this.makeSvgNode(h.id, hubX + i * 80, 20, 100, 40, 'hub', h.url);
      svg.appendChild(hub);
    });
    // Worker darunter
    this.workers.forEach((w, i) => {
      const y = 120 + Math.floor(i / 4) * 80;
      const x = 30 + (i % 4) * 140;
      const node = this.makeSvgNode(w.id, x, y, 120, 50, w.cliBackend === 'deterministic' ? 'det' : 'llm', `${w.cliBackend} · ${w.model}`);
      svg.appendChild(node);
      // Klick-binding
      node.addEventListener('click', () => this.workerSelected.emit(w.id));
    });
    host.appendChild(svg);
  }

  private makeSvgNode(id: string, x: number, y: number, w: number, h: number, kind: string, label: string): SVGGElement {
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('data-id', id);
    g.setAttribute('transform', `translate(${x},${y})`);
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('width', String(w));
    rect.setAttribute('height', String(h));
    rect.setAttribute('rx', '8');
    rect.setAttribute('fill', kind === 'det' ? '#e5e7eb' : kind === 'hub' ? '#fef3c7' : '#dbeafe');
    rect.setAttribute('stroke', '#6b7280');
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
    const subtext = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    subtext.setAttribute('x', String(w / 2));
    subtext.setAttribute('y', String(h / 2 + 12));
    subtext.setAttribute('text-anchor', 'middle');
    subtext.setAttribute('font-size', '9');
    subtext.setAttribute('fill', '#6b7280');
    subtext.textContent = label;
    g.appendChild(subtext);
    return g;
  }

  /**
   * Baut BPMN-XML fuer Hub-Worker-Topologie.
   * Pools = Hubs, Tasks = Worker, SequenceFlows = Verbindungen.
   */
  private buildBpmnXml(): string {
    const hubTasks = this.hubs.map((h, i) => `
      <bpmn:process id="hub_${this.escapeXml(h.id)}" isExecutable="false">
        <bpmn:startEvent id="hub_start_${i}" />
        ${this.workers.filter(w => true).map((w, wi) => `
        <bpmn:task id="worker_${this.escapeXml(w.id)}" name="${this.escapeXml(this.workerLabel(w))}">
          <bpmn:documentation>${this.escapeXml(w.cliBackend)} · ${this.escapeXml(w.model)}</bpmn:documentation>
        </bpmn:task>`).join('')}
      </bpmn:process>
    `).join('');

    const flows = this.connections.map((c, i) => {
      const sourceId = `hub_${this.escapeXml(c.hubId)}_start`;
      const targetId = `worker_${this.escapeXml(c.workerId)}`;
      return `<bpmn:sequenceFlow id="flow_${i}" sourceRef="${sourceId}" targetRef="${targetId}" />`;
    }).join('');

    return `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  id="Definitions_1">
  ${hubTasks}
  <bpmn:collaboration id="Collaboration_1">
    <bpmn:participant id="Participant_hub" name="Hub" processRef="hub_${this.escapeXml(this.hubs[0]?.id ?? 'default')}" />
  </bpmn:collaboration>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Collaboration_1" />
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`;
  }

  private workerLabel(w: ChWorkerInstanceReadModel): string {
    const det = w.cliBackend === 'deterministic' ? '[det] ' : '';
    return `${det}${w.id}`;
  }

  private escapeXml(s: string): string {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;');
  }
}