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

import { GraphDomainLegendEntry, GraphLegendToggle, GraphNodeSizeLegendModel } from './graph-legend.models';
import { GraphNodeSizeLegendComponent } from './graph-node-size-legend.component';

let nextDomainLegendId = 0;

@Component({
  standalone: true,
  selector: 'app-graph-domain-legend',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [A11yModule, GraphNodeSizeLegendComponent],
  template: `
    <button
      #trigger
      type="button"
      class="legend-trigger"
      [attr.aria-controls]="panelId"
      [attr.aria-expanded]="open"
      data-testid="graph-domain-legend-trigger"
      (click)="setOpen(!open)"
    >
      Domains &amp; Knotengröße
    </button>

    @if (open) {
      <aside
        [id]="panelId"
        class="legend-panel"
        role="dialog"
        aria-modal="true"
        [attr.aria-labelledby]="titleId"
        data-testid="graph-domain-legend-drawer"
        cdkTrapFocus
        [cdkTrapFocusAutoCapture]="true"
        (keydown.escape)="closeAndRestoreFocus()"
      >
        <header>
          <h2 [id]="titleId">Domain-Legende</h2>
          <button type="button" class="icon-button" aria-label="Domain-Legende schließen" (click)="closeAndRestoreFocus()">×</button>
        </header>

        <div class="legend-scroll">
          <div class="legend-actions">
            <button type="button" (click)="setEveryDomain(true)">Alle anzeigen</button>
            <button type="button" (click)="setEveryDomain(false)">Alle ausblenden</button>
            <button type="button" (click)="clearHighlight.emit()">Hervorhebung löschen</button>
          </div>

          <ul class="entry-list" aria-label="Domains">
            @for (entry of entries; track entry.domainId) {
              <li
                class="domain-entry"
                [class.is-hidden]="!entry.visible"
                (mouseenter)="domainHovered.emit(entry.domainId)"
                (mouseleave)="domainHovered.emit(null)"
                (focusin)="domainHovered.emit(entry.domainId)"
                (focusout)="domainHovered.emit(null)"
              >
                <label>
                  <input
                    type="checkbox"
                    [checked]="entry.visible"
                    [attr.aria-label]="entry.label + ' anzeigen'"
                    (change)="toggleDomain(entry.domainId, $any($event.target).checked)"
                  />
                  <span class="marker" aria-hidden="true" [style.background-color]="entry.color">{{ entry.marker }}</span>
                  <span class="entry-name">{{ entry.label }}</span>
                </label>
                @if (entry.domainId !== entry.label) {
                  <p class="domain-id">{{ entry.domainId }}</p>
                }
                <dl>
                  <div><dt>Sichtbar</dt><dd>{{ entry.visibleNodes }}/{{ entry.totalNodes }}</dd></div>
                  <div><dt>Intern</dt><dd>{{ entry.internalEdges }}</dd></div>
                  <div><dt>Ausgehend</dt><dd>{{ entry.outgoingExternalEdges }}</dd></div>
                  <div><dt>Eingehend</dt><dd>{{ entry.incomingExternalEdges }}</dd></div>
                  <div><dt>Score-Summe</dt><dd>{{ entry.sumNodeScore.toFixed(3) }}</dd></div>
                </dl>
              </li>
            } @empty {
              <li class="empty">Keine Domaininformationen verfügbar.</li>
            }
          </ul>

          @if (showNodeSize) {
            <app-graph-node-size-legend [model]="nodeSizeLegend" />
          }
        </div>
      </aside>
    }
  `,
  styles: [`
    :host { display: inline-flex; position: relative; }
    button { font: inherit; }
    .legend-trigger, .legend-actions button {
      border: 1px solid #cbd5e1; border-radius: 4px; background: #fff; color: #334155;
      cursor: pointer; padding: 3px 9px; font-size: .78rem;
    }
    button:focus-visible, input:focus-visible { outline: 3px solid #38bdf8; outline-offset: 2px; }
    .legend-panel {
      box-sizing: border-box;
      position: fixed; z-index: 420; top: 4.25rem; right: 1rem; width: min(380px, calc(100vw - 2rem));
      max-height: calc(100dvh - 5.25rem); display: flex; flex-direction: column;
      background: #fff; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 8px;
      box-shadow: 0 12px 36px rgba(15, 23, 42, .22);
    }
    header { display: flex; align-items: center; justify-content: space-between; padding: .65rem .75rem; border-bottom: 1px solid #e2e8f0; }
    h2 { margin: 0; font-size: .95rem; }
    .icon-button { border: 0; border-radius: 4px; background: transparent; font-size: 1.25rem; line-height: 1; cursor: pointer; }
    .legend-scroll { overflow: auto; padding: .7rem; }
    .legend-actions { display: flex; flex-wrap: wrap; gap: .35rem; margin-bottom: .65rem; }
    .entry-list { list-style: none; padding: 0; margin: 0 0 .85rem; display: grid; gap: .45rem; }
    .domain-entry { border: 1px solid #e2e8f0; border-radius: 6px; padding: .45rem; }
    .domain-entry.is-hidden { background: #f8fafc; color: #64748b; }
    label { display: flex; align-items: center; gap: .4rem; cursor: pointer; font-size: .78rem; }
    .marker { display: inline-grid; place-items: center; width: 20px; height: 20px; border-radius: 4px; color: #fff; text-shadow: 0 1px 2px #000; font-size: .68rem; flex: 0 0 auto; }
    .entry-name { font-weight: 650; min-width: 0; overflow-wrap: anywhere; }
    .domain-id { margin: .2rem 0 0 1.7rem; font: .68rem ui-monospace, monospace; color: #64748b; overflow-wrap: anywhere; }
    dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .15rem .6rem; margin: .35rem 0 0 1.7rem; }
    dl div { display: flex; justify-content: space-between; gap: .3rem; font-size: .68rem; }
    dt { color: #64748b; } dd { margin: 0; font-variant-numeric: tabular-nums; }
    .empty { color: #64748b; font-size: .76rem; padding: .5rem 0; }
    @media (max-width: 600px) {
      .legend-panel { inset: auto 0 0 0; width: 100%; max-height: 78dvh; border-radius: 12px 12px 0 0; }
    }
  `],
})
export class GraphDomainLegendComponent {
  @ViewChild('trigger', { read: ElementRef }) private trigger?: ElementRef<HTMLButtonElement>;
  readonly panelId = `graph-domain-legend-${++nextDomainLegendId}`;
  readonly titleId = `${this.panelId}-title`;

  @Input() open = false;
  @Input() entries: readonly GraphDomainLegendEntry[] = [];
  @Input() nodeSizeLegend: GraphNodeSizeLegendModel | null = null;
  @Input() showNodeSize = true;

  @Output() openChange = new EventEmitter<boolean>();
  @Output() domainVisibilityChange = new EventEmitter<GraphLegendToggle>();
  @Output() domainHovered = new EventEmitter<string | null>();
  @Output() clearHighlight = new EventEmitter<void>();

  setOpen(open: boolean): void {
    this.open = open;
    this.openChange.emit(open);
  }

  closeAndRestoreFocus(): void {
    this.setOpen(false);
    queueMicrotask(() => this.trigger?.nativeElement.focus());
  }

  toggleDomain(domainId: string, visible: boolean): void {
    this.domainVisibilityChange.emit({ id: domainId, visible });
  }

  setEveryDomain(visible: boolean): void {
    for (const entry of this.entries) {
      if (entry.visible !== visible) this.toggleDomain(entry.domainId, visible);
    }
  }
}
