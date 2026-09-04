import { CommonModule } from '@angular/common';
import {
  AfterViewInit,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  NgZone,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
  ViewChild,
  inject,
} from '@angular/core';
import { MapChart } from 'echarts/charts';
import { LegendComponent, TooltipComponent, VisualMapComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

import { GeoMapGeometry, GeoMapProjection } from './geomap.models';

echarts.use([MapChart, LegendComponent, TooltipComponent, VisualMapComponent, CanvasRenderer]);

@Component({
  selector: 'app-geomap-renderer',
  standalone: true,
  imports: [CommonModule],
  template: `
    <figure>
      <div class="toolbar">
        <button type="button" (click)="reset()">Zoom zurücksetzen</button>
        <span aria-live="polite">{{ selectedName || 'Keine Region ausgewählt' }}</span>
      </div>
      <div #chart class="map" role="img" [attr.aria-label]="ariaLabel"></div>
      <figcaption>{{ projection?.data_attribution }} · {{ projection?.map_attribution }}</figcaption>
    </figure>
    <div class="region-list" aria-label="Kartenregionen">
      @for (region of projection?.values || []; track region.region_id) {
        <button
          type="button"
          [class.selected]="region.name === selectedName"
          (click)="select(region.name)"
          [attr.aria-pressed]="region.name === selectedName">
          {{ region.name }}: {{ region.value | number:'1.0-3' }}
        </button>
      }
    </div>
  `,
  styles: [`
    figure { margin:0; min-width:0; }
    .map { width:100%; min-height:420px; border:1px solid var(--border-color, #667); border-radius:8px; }
    .toolbar { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:8px; }
    figcaption { margin-top:6px; color:var(--muted, #596579); font-size:.8rem; overflow-wrap:anywhere; }
    .region-list { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:4px; max-height:180px; overflow:auto; margin-top:10px; }
    .region-list button { text-align:left; }
    .region-list button.selected { outline:2px solid var(--primary, #5877e8); }
    @media (max-width: 680px) { .map { min-height:300px; } }
  `],
})
export class GeoMapRendererComponent implements AfterViewInit, OnChanges, OnDestroy {
  @Input() mapId = '';
  @Input() geometry: GeoMapGeometry | null = null;
  @Input() projection: GeoMapProjection | null = null;
  @Output() readonly regionSelect = new EventEmitter<string>();
  @ViewChild('chart') private chartElement?: ElementRef<HTMLDivElement>;

  selectedName = '';
  private chart?: echarts.ECharts;
  private readonly zone = inject(NgZone);
  private readonly resize = () => this.chart?.resize();

  get ariaLabel(): string {
    const matched = this.projection?.report.matched.length || 0;
    return `Interaktive Karte ${this.mapId} mit ${matched} zugeordneten Regionen`;
  }

  ngAfterViewInit(): void {
    this.zone.runOutsideAngular(() => {
      this.chart = echarts.init(this.chartElement!.nativeElement, undefined, { renderer: 'canvas' });
      this.chart.on('click', params => {
        const name = String(params.name || '');
        this.zone.run(() => this.select(name));
      });
      window.addEventListener('resize', this.resize);
      this.render();
    });
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['geometry'] || changes['projection'] || changes['mapId']) this.render();
  }

  ngOnDestroy(): void {
    window.removeEventListener('resize', this.resize);
    this.chart?.dispose();
  }

  reset(): void {
    this.selectedName = '';
    this.chart?.dispatchAction({ type: 'restore' });
  }

  select(name: string): void {
    if (!name) return;
    this.selectedName = name;
    this.chart?.dispatchAction({ type: 'mapSelect', name });
    this.regionSelect.emit(name);
  }

  private render(): void {
    if (!this.chart || !this.geometry || !this.projection || !this.mapId) return;
    const source = this.geometry.type === 'AnantaSvgMap' ? { svg: this.geometry.svg } : this.geometry;
    echarts.registerMap(this.mapId, source as never);
    const data = this.projection.values.map(item => ({
      name: item.name,
      value: item.value,
      regionId: item.region_id,
      sourceRows: item.source_rows,
    }));
    const numeric = data.map(item => item.value);
    const min = numeric.length ? Math.min(...numeric) : 0;
    const max = numeric.length ? Math.max(...numeric) : 1;
    this.chart.setOption({
      animation: false,
      tooltip: {
        trigger: 'item',
        formatter: (params: { data?: { name: string; value: number; sourceRows: number } }) => {
          const item = params.data;
          return item ? `${item.name}<br>${item.value}<br>Status: zugeordnet · Zeilen: ${item.sourceRows}` : 'Keine Daten';
        },
      },
      visualMap: { min, max: max === min ? min + 1 : max, calculable: true, orient: 'horizontal', left: 'center' },
      series: [{
        type: 'map',
        map: this.mapId,
        roam: true,
        selectedMode: 'single',
        data,
        nameProperty: 'name',
        itemStyle: { areaColor: '#d9dee8', borderColor: '#667085' },
        emphasis: { label: { show: true } },
      }],
    }, true);
  }
}
