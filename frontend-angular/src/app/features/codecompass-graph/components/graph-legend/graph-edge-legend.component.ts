import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  Output,
  ViewChild,
} from '@angular/core';
import { A11yModule } from '@angular/cdk/a11y';

import {
  GraphEdgeLegendEntry,
  GraphEdgeWidthLegendModel,
  GraphLegendToggle,
} from './graph-legend.models';

let nextEdgeLegendId = 0;

@Component({
  standalone: true,
  selector: 'app-graph-edge-legend',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [A11yModule],
  template: `
    <button
      #trigger
      type="button"
      class="legend-trigger"
      [attr.aria-controls]="panelId"
      [attr.aria-expanded]="open"
      data-testid="graph-edge-legend-trigger"
      (click)="setOpen(!open)"
    >Kanten &amp; Dicke</button>

    @if (open) {
      <aside
        [id]="panelId"
        class="legend-panel"
        role="dialog"
        aria-modal="true"
        [attr.aria-labelledby]="titleId"
        data-testid="graph-edge-legend-drawer"
        cdkTrapFocus
        [cdkTrapFocusAutoCapture]="true"
        (keydown.escape)="closeAndRestoreFocus()"
      >
        <header>
          <h2 [id]="titleId">Kanten-Legende</h2>
          <button type="button" class="icon-button" aria-label="Kanten-Legende schließen" (click)="closeAndRestoreFocus()">×</button>
        </header>
        <div class="legend-scroll">
          <div class="legend-actions">
            <button type="button" (click)="setEveryRelation(true)">Alle anzeigen</button>
            <button type="button" (click)="setEveryRelation(false)">Alle ausblenden</button>
            <button type="button" (click)="clearHighlight.emit()">Hervorhebung löschen</button>
          </div>

          <ul class="entry-list" aria-label="Relationen">
            @for (entry of entries; track entry.rawEdgeType) {
              <li
                class="edge-entry"
                [class.is-hidden]="!entry.visible"
                (mouseenter)="relationHovered.emit(entry.rawEdgeType)"
                (mouseleave)="relationHovered.emit(null)"
                (focusin)="relationHovered.emit(entry.rawEdgeType)"
                (focusout)="relationHovered.emit(null)"
              >
                <label>
                  <input
                    type="checkbox"
                    [checked]="entry.visible"
                    [attr.aria-label]="entry.label + ' anzeigen'"
                    (change)="toggleRelation(entry.rawEdgeType, $any($event.target).checked)"
                  />
                  <span class="line-sample" aria-hidden="true" [style.border-top-color]="entry.color"></span>
                  <span class="marker" aria-hidden="true">{{ entry.marker }}</span>
                  <span class="entry-name">{{ entry.label }}</span>
                </label>
                <p class="raw-type">{{ entry.rawEdgeType }}</p>
                @if (entry.semanticState === 'semantically_unknown') {
                  <p class="unknown">Semantik unbekannt · neutraler Fallback</p>
                }
                <dl>
                  <div><dt>Sichtbar</dt><dd>{{ entry.visibleEdges }}/{{ entry.totalEdges }}</dd></div>
                  <div><dt>Multiplizität</dt><dd>{{ entry.multiplicitySum }}</dd></div>
                </dl>
              </li>
            } @empty {
              <li class="empty">Keine Relationen verfügbar.</li>
            }
          </ul>

          @if (showEdgeWidth) {
          <section aria-label="Kantendicke">
            <h3>Kantendicke</h3>
            @if (!widthLegend) {
              <p class="empty">Keine Dickenprojektion verfügbar.</p>
            } @else {
              <div class="width-references" aria-label="Dickenreferenzen">
                @for (reference of widthLegend.references; track reference.label) {
                  <span><i aria-hidden="true" [style.border-top-width.px]="reference.value"></i>{{ reference.label }} · {{ reference.value.toFixed(1) }}</span>
                }
              </div>
              @if (widthLegend.metricsVisible) {
              <ul class="metric-list" aria-label="Aktive Kantenmetriken">
                @for (metric of widthLegend.metrics; track metric.metricId) {
                  <li>
                    <strong>{{ metric.label }}</strong> · Gewicht {{ metric.weight }}
                    @if (metric.partialScore !== undefined) { · Teilscore {{ metric.partialScore.toFixed(3) }} }
                    · {{ metric.availability }}
                    @if (metric.reasonCode) { · {{ metric.reasonCode }} }
                  </li>
                } @empty {
                  <li>Fallback-Minimaldicke – keine aktive verfügbare Metrik</li>
                }
              </ul>
              }
            }
          </section>
          }
        </div>
      </aside>
    }
  `,
  styles: [`
    :host { display: inline-flex; position: relative; }
    button { font: inherit; }
    .legend-trigger, .legend-actions button { border: 1px solid #cbd5e1; border-radius: 4px; background: #fff; color: #334155; cursor: pointer; padding: 3px 9px; font-size: .78rem; }
    button:focus-visible, input:focus-visible { outline: 3px solid #38bdf8; outline-offset: 2px; }
    .legend-panel { box-sizing: border-box; position: fixed; z-index: 420; top: 4.25rem; right: 1rem; width: min(400px, calc(100vw - 2rem)); max-height: calc(100dvh - 5.25rem); display: flex; flex-direction: column; background: #fff; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 8px; box-shadow: 0 12px 36px rgba(15,23,42,.22); }
    header { display: flex; align-items: center; justify-content: space-between; padding: .65rem .75rem; border-bottom: 1px solid #e2e8f0; }
    h2, h3 { margin: 0; font-size: .95rem; } h3 { margin-bottom: .4rem; font-size: .85rem; }
    .icon-button { border: 0; border-radius: 4px; background: transparent; font-size: 1.25rem; line-height: 1; cursor: pointer; }
    .legend-scroll { overflow: auto; padding: .7rem; }
    .legend-actions { display: flex; flex-wrap: wrap; gap: .35rem; margin-bottom: .65rem; }
    .entry-list, .metric-list { list-style: none; padding: 0; margin: 0 0 .85rem; display: grid; gap: .45rem; }
    .edge-entry { border: 1px solid #e2e8f0; border-radius: 6px; padding: .45rem; }
    .edge-entry.is-hidden { background: #f8fafc; color: #64748b; }
    label { display: flex; align-items: center; gap: .35rem; cursor: pointer; font-size: .78rem; }
    .line-sample { width: 28px; border-top: 3px solid; flex: 0 0 auto; }
    .marker { width: 1rem; text-align: center; font-size: .75rem; }
    .entry-name { font-weight: 650; overflow-wrap: anywhere; }
    .raw-type, .unknown { margin: .25rem 0 0 1.7rem; font: .68rem ui-monospace, monospace; overflow-wrap: anywhere; }
    .unknown { color: #b45309; font-family: inherit; }
    dl { display: flex; gap: .8rem; margin: .25rem 0 0 1.7rem; }
    dl div { display: flex; gap: .25rem; font-size: .68rem; } dt { color: #64748b; } dd { margin: 0; font-variant-numeric: tabular-nums; }
    .width-references { display: grid; gap: .35rem; font-size: .7rem; }
    .width-references span { display: grid; grid-template-columns: 65px 1fr; align-items: center; gap: .45rem; }
    .width-references i { border-top: solid #64748b; }
    .metric-list { margin-top: .55rem; font-size: .7rem; }
    .empty { color: #64748b; font-size: .76rem; }
    @media (max-width: 600px) { .legend-panel { inset: auto 0 0 0; width: 100%; max-height: 78dvh; border-radius: 12px 12px 0 0; } }
  `],
})
export class GraphEdgeLegendComponent {
  @ViewChild('trigger', { read: ElementRef }) private trigger?: ElementRef<HTMLButtonElement>;
  readonly panelId = `graph-edge-legend-${++nextEdgeLegendId}`;
  readonly titleId = `${this.panelId}-title`;

  @Input() open = false;
  @Input() entries: readonly GraphEdgeLegendEntry[] = [];
  @Input() widthLegend: GraphEdgeWidthLegendModel | null = null;
  @Input() showEdgeWidth = true;

  @Output() openChange = new EventEmitter<boolean>();
  @Output() relationVisibilityChange = new EventEmitter<GraphLegendToggle>();
  @Output() relationHovered = new EventEmitter<string | null>();
  @Output() clearHighlight = new EventEmitter<void>();

  setOpen(open: boolean): void {
    this.open = open;
    this.openChange.emit(open);
  }

  closeAndRestoreFocus(): void {
    this.setOpen(false);
    queueMicrotask(() => this.trigger?.nativeElement.focus());
  }

  toggleRelation(rawEdgeType: string, visible: boolean): void {
    this.relationVisibilityChange.emit({ id: rawEdgeType, visible });
  }

  setEveryRelation(visible: boolean): void {
    for (const entry of this.entries) {
      if (entry.visible !== visible) this.toggleRelation(entry.rawEdgeType, visible);
    }
  }
}
