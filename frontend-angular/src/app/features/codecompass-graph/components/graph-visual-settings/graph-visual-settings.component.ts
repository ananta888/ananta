import { A11yModule } from '@angular/cdk/a11y';
import { NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  OnDestroy,
  Output,
  ViewChild,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { GraphMetricCapability } from '../../models/graph-visual-metrics.model';
import {
  GraphVisualMetricId,
  GraphVisualProfile,
  GraphVisualProfilePresetId,
  WeightedMetricConfig,
} from '../../models/graph-visual-profile.model';
import { GraphVisualProfileFacade } from '../../services/graph-visual-profile.facade';

let nextSettingsId = 0;

export interface GraphVisualDomainOption {
  readonly domainId: string;
  readonly label: string;
  readonly color: string;
}

@Component({
  standalone: true,
  selector: 'app-graph-visual-settings',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [A11yModule, FormsModule, NgTemplateOutlet],
  template: `
    <button
      #trigger
      type="button"
      class="settings-trigger"
      data-testid="graph-visual-settings-trigger"
      [attr.aria-controls]="panelId"
      [attr.aria-expanded]="open"
      (click)="setOpen(!open)"
    >Visualisierung</button>

    @if (open) {
      <aside
        [id]="panelId"
        class="settings-panel"
        role="dialog"
        aria-modal="true"
        [attr.aria-labelledby]="titleId"
        data-testid="graph-visual-settings-drawer"
        cdkTrapFocus
        [cdkTrapFocusAutoCapture]="true"
        (keydown.escape)="closeAndRestoreFocus()"
      >
        <header>
          <h2 [id]="titleId">Visualisierungs-Einstellungen</h2>
          <button type="button" class="icon-button" aria-label="Visualisierungs-Einstellungen schließen" (click)="closeAndRestoreFocus()">×</button>
        </header>

        <div class="settings-scroll">
          <section aria-label="Profil">
            <h3>Profil</h3>
            <label class="field-row">
              <span>Preset</span>
              <select
                data-testid="graph-visual-profile-preset"
                aria-label="Visualisierungs-Preset"
                [ngModel]="profile().profileId"
                (ngModelChange)="selectPreset($event)"
              >
                @for (entry of presetEntries; track entry.id) {
                  <option [value]="entry.id">{{ entry.name }}</option>
                }
              </select>
            </label>
            <div class="action-row">
              <button type="button" (click)="save()">Speichern</button>
              <button type="button" (click)="reset()">Zurücksetzen</button>
              <button type="button" (click)="exportProfile()">Exportieren</button>
              <label class="file-button">
                Importieren
                <input type="file" accept="application/json,.json" (change)="importFile($event)" />
              </label>
            </div>
            @if (exportedProfile()) {
              <label class="export-field">
                <span>Exportiertes Profil</span>
                <textarea readonly rows="4" [value]="exportedProfile()" aria-label="Exportiertes Visualisierungsprofil"></textarea>
              </label>
            }
          </section>

          <section aria-label="Knotenmetriken">
            <h3>Knotenmetriken</h3>
            @for (metric of profile().nodeMetrics; track metric.metricId) {
              <ng-container [ngTemplateOutlet]="metricRow" [ngTemplateOutletContext]="{ metric: metric, entity: 'node' }" />
            }
          </section>

          <section aria-label="Kantenmetriken">
            <h3>Kantenmetriken</h3>
            @for (metric of profile().edgeMetrics; track metric.metricId) {
              <ng-container [ngTemplateOutlet]="metricRow" [ngTemplateOutletContext]="{ metric: metric, entity: 'edge' }" />
            }
          </section>

          <ng-template #metricRow let-metric="metric" let-entity="entity">
            <fieldset class="metric-row" [attr.data-metric-id]="metric.metricId">
              <legend>{{ metric.metricId }}</legend>
              <label>
                <input
                  type="checkbox"
                  [checked]="metric.enabled"
                  [disabled]="metricDisabled(metric.metricId)"
                  (change)="patchMetric(entity, metric.metricId, { enabled: $any($event.target).checked })"
                /> Aktiv
              </label>
              <label>Gewicht
                <input type="number" min="0" max="100" step="0.1" [value]="metric.weight"
                       [disabled]="metricDisabled(metric.metricId)"
                       (change)="patchMetric(entity, metric.metricId, { weight: numberValue($event) })" />
              </label>
              <label>Normalisierung
                <select [value]="metric.normalization" [disabled]="metricDisabled(metric.metricId)"
                        (change)="patchMetric(entity, metric.metricId, { normalization: $any($event.target).value })">
                  <option value="linear">linear</option>
                  <option value="log1p">log1p</option>
                  <option value="sqrt">sqrt</option>
                </select>
              </label>
              <label>Richtung
                <select [value]="metric.direction" [disabled]="metricDisabled(metric.metricId)"
                        (change)="patchMetric(entity, metric.metricId, { direction: $any($event.target).value })">
                  <option value="normal">normal</option>
                  <option value="inverse">inverse</option>
                </select>
              </label>
              @if (capability(metric.metricId); as capability) {
                <p class="capability" [attr.data-availability]="capability.availability">
                  {{ capability.availability }} · {{ capability.source }}
                  @if (capability.reasonCode) { · {{ capability.reasonCode }} }
                </p>
              } @else {
                <p class="capability" data-availability="unavailable">unavailable · capability_missing</p>
              }
            </fieldset>
          </ng-template>

          <section aria-label="Renderbereiche">
            <h3>Renderbereiche</h3>
            <div class="range-grid">
              <label>Knotengröße min.
                <input type="number" min="1" max="100" [value]="profile().nodeSizeRange.min" (change)="patchRange('nodeSizeRange', 'min', numberValue($event))" />
              </label>
              <label>Knotengröße max.
                <input type="number" min="1" max="100" [value]="profile().nodeSizeRange.max" (change)="patchRange('nodeSizeRange', 'max', numberValue($event))" />
              </label>
              <label>Kantendicke min.
                <input type="number" min="0.1" max="20" step="0.1" [value]="profile().edgeThicknessRange.min" (change)="patchRange('edgeThicknessRange', 'min', numberValue($event))" />
              </label>
              <label>Kantendicke max.
                <input type="number" min="0.1" max="20" step="0.1" [value]="profile().edgeThicknessRange.max" (change)="patchRange('edgeThicknessRange', 'max', numberValue($event))" />
              </label>
            </div>
          </section>

          <section aria-label="Hervorhebung">
            <h3>Hervorhebung</h3>
            <div class="range-grid">
              @for (key of highlightKeys; track key) {
                <label>{{ key }}
                  <input type="range" min="1" max="5" step="0.05" [value]="profile().highlightFactors[key]"
                         (input)="patchHighlight(key, numberValue($event))" />
                  <output>{{ profile().highlightFactors[key].toFixed(2) }}</output>
                </label>
              }
            </div>
          </section>

          <section aria-label="Domain-Farben">
            <h3>Domain-Farben</h3>
            <div class="color-grid">
              @for (domain of domainOptions; track domain.domainId) {
                <label>
                  <span>{{ domain.label }}</span>
                  <input
                    type="color"
                    [attr.aria-label]="'Farbe für ' + domain.label"
                    [value]="profile().domainColorOverrides[domain.domainId] || domain.color"
                    (change)="patchDomainColor(domain.domainId, $any($event.target).value)"
                  />
                  @if (profile().domainColorOverrides[domain.domainId]) {
                    <button type="button" (click)="clearDomainColor(domain.domainId)">Automatisch</button>
                  }
                </label>
              } @empty {
                <p class="capability">Keine Domains im Graph vorhanden.</p>
              }
            </div>
          </section>

          <section aria-label="Legenden">
            <h3>Legenden</h3>
            @for (entry of legendEntries; track entry.key) {
              <label class="check-row">
                <input type="checkbox" [checked]="profile().legend[entry.key]"
                       (change)="patchLegend(entry.key, $any($event.target).checked)" />
                {{ entry.label }}
              </label>
            }
          </section>

          @if (issues().length) {
            <section class="issues" role="alert" aria-label="Profilfehler">
              <h3>Profil konnte nicht übernommen werden</h3>
              <ul>
                @for (issue of issues(); track issue.path + issue.reasonCode) {
                  <li><code>{{ issue.path }}</code> · {{ issue.reasonCode }} · {{ issue.message }}</li>
                }
              </ul>
            </section>
          }
        </div>
      </aside>
    }
  `,
  styles: [`
    :host { display: inline-flex; }
    button, input, select, textarea { font: inherit; }
    button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 3px solid #38bdf8; outline-offset: 2px; }
    .settings-trigger, .action-row button, .file-button { border: 1px solid #cbd5e1; border-radius: 4px; background: #fff; color: #334155; cursor: pointer; padding: 3px 9px; font-size: .78rem; }
    .settings-panel { box-sizing: border-box; position: fixed; z-index: 430; top: 3.5rem; right: 0; width: min(520px, 100vw); height: calc(100dvh - 3.5rem); display: flex; flex-direction: column; background: #fff; color: #0f172a; border-left: 1px solid #cbd5e1; box-shadow: -12px 0 36px rgba(15,23,42,.2); }
    header { display: flex; align-items: center; justify-content: space-between; padding: .75rem; border-bottom: 1px solid #e2e8f0; }
    h2, h3 { margin: 0; } h2 { font-size: 1rem; } h3 { font-size: .85rem; margin-bottom: .5rem; }
    .icon-button { border: 0; background: transparent; cursor: pointer; font-size: 1.3rem; }
    .settings-scroll { overflow: auto; padding: .75rem; }
    section { padding: .65rem 0; border-bottom: 1px solid #e2e8f0; }
    .field-row, .check-row { display: flex; align-items: center; gap: .5rem; font-size: .78rem; }
    .field-row { justify-content: space-between; }
    .action-row { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .5rem; }
    .file-button input { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
    .export-field { display: grid; gap: .25rem; margin-top: .5rem; font-size: .75rem; }
    .export-field textarea { width: 100%; box-sizing: border-box; font-family: ui-monospace, monospace; font-size: .65rem; }
    .metric-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .45rem; margin: 0 0 .55rem; padding: .5rem; border: 1px solid #e2e8f0; border-radius: 6px; }
    .metric-row legend { font: 600 .75rem ui-monospace, monospace; padding: 0 .2rem; }
    .metric-row label { display: grid; gap: .2rem; font-size: .7rem; }
    .metric-row label:first-of-type { display: flex; align-items: center; }
    .capability { grid-column: 1 / -1; margin: 0; color: #475569; font-size: .68rem; overflow-wrap: anywhere; }
    [data-availability='unavailable'], [data-availability='not_applicable'] { color: #b45309; }
    [data-availability='approximate'] { color: #0369a1; }
    .range-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .5rem; }
    .range-grid label { display: grid; gap: .2rem; font-size: .72rem; }
    .color-grid { display: grid; gap: .35rem; }
    .color-grid label { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: .45rem; font-size: .72rem; }
    .color-grid label span { overflow-wrap: anywhere; }
    .color-grid button { border: 1px solid #cbd5e1; border-radius: 3px; background: #fff; padding: 2px 5px; font-size: .68rem; cursor: pointer; }
    .issues { color: #991b1b; } .issues ul { padding-left: 1.2rem; font-size: .7rem; }
    code { font-size: .68rem; }
    @media (max-width: 600px) {
      .settings-panel { inset: auto 0 0 0; width: 100%; height: min(82dvh, 700px); border: 1px solid #cbd5e1; border-radius: 12px 12px 0 0; }
      .metric-row, .range-grid { grid-template-columns: 1fr; }
    }
  `],
})
export class GraphVisualSettingsComponent implements OnDestroy {
  @ViewChild('trigger', { read: ElementRef }) private trigger?: ElementRef<HTMLButtonElement>;

  readonly facade = inject(GraphVisualProfileFacade);
  readonly profile = this.facade.activeProfile;
  readonly issues = this.facade.lastIssues;
  readonly exportedProfile = signal('');
  readonly panelId = `graph-visual-settings-${++nextSettingsId}`;
  readonly titleId = `${this.panelId}-title`;
  readonly highlightKeys = ['hover', 'selected', 'connected'] as const;
  readonly presetEntries = Object.entries(this.facade.presets).map(([id, profile]) => ({
    id: id as GraphVisualProfilePresetId,
    name: profile.name,
  }));
  readonly legendEntries = [
    { key: 'showDomains', label: 'Domains anzeigen' },
    { key: 'showRelations', label: 'Relationen anzeigen' },
    { key: 'showMetrics', label: 'Metrikdetails anzeigen' },
    { key: 'showUnavailable', label: 'Nicht verfügbare Metriken anzeigen' },
  ] as const;

  @Input() open = false;
  @Input() metricCapabilities: readonly GraphMetricCapability[] = [];
  @Input() domainOptions: readonly GraphVisualDomainOption[] = [];
  @Output() openChange = new EventEmitter<boolean>();

  private frameHandle: number | null = null;
  private frameUsesTimeout = false;
  private pendingProfile: GraphVisualProfile | null = null;

  ngOnDestroy(): void {
    this.cancelScheduledFrame();
    this.pendingProfile = null;
  }

  private cancelScheduledFrame(): void {
    if (this.frameHandle === null) return;
    if (this.frameUsesTimeout) {
      clearTimeout(this.frameHandle);
    } else if (typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(this.frameHandle);
    }
    this.frameHandle = null;
    this.frameUsesTimeout = false;
  }

  setOpen(open: boolean): void {
    this.open = open;
    this.openChange.emit(open);
  }

  closeAndRestoreFocus(): void {
    this.setOpen(false);
    queueMicrotask(() => this.trigger?.nativeElement.focus());
  }

  capability(metricId: string): GraphMetricCapability | undefined {
    return this.metricCapabilities.find(item => item.metricId === metricId);
  }

  metricDisabled(metricId: string): boolean {
    const availability = this.capability(metricId)?.availability ?? 'unavailable';
    return availability === 'unavailable' || availability === 'not_applicable';
  }

  numberValue(event: Event): number {
    return Number((event.target as HTMLInputElement).value);
  }

  selectPreset(presetId: GraphVisualProfilePresetId): void {
    this.discardPendingProfile();
    this.facade.selectPreset(presetId);
  }

  save(): void {
    this.flushPendingProfile();
    this.facade.save();
  }

  reset(): void {
    this.discardPendingProfile();
    this.facade.reset();
  }

  exportProfile(): void {
    this.flushPendingProfile();
    this.exportedProfile.set(this.facade.exportProfile());
  }

  async importFile(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.item(0);
    if (!file) return;
    this.discardPendingProfile();
    await this.facade.importProfileFile(file);
    input.value = '';
  }

  patchMetric(
    entity: 'node' | 'edge',
    metricId: GraphVisualMetricId,
    patch: Partial<Pick<WeightedMetricConfig, 'enabled' | 'weight' | 'normalization' | 'direction'>>,
  ): void {
    const profile = this.draftProfile();
    const key = entity === 'node' ? 'nodeMetrics' : 'edgeMetrics';
    this.schedule({
      ...profile,
      [key]: profile[key].map(metric => metric.metricId === metricId ? { ...metric, ...patch } : metric),
    });
  }

  patchRange(key: 'nodeSizeRange' | 'edgeThicknessRange', bound: 'min' | 'max', value: number): void {
    const profile = this.draftProfile();
    this.schedule({ ...profile, [key]: { ...profile[key], [bound]: value } });
  }

  patchHighlight(key: 'hover' | 'selected' | 'connected', value: number): void {
    const profile = this.draftProfile();
    this.schedule({ ...profile, highlightFactors: { ...profile.highlightFactors, [key]: value } });
  }

  patchLegend(key: keyof GraphVisualProfile['legend'], value: boolean): void {
    const profile = this.draftProfile();
    this.schedule({ ...profile, legend: { ...profile.legend, [key]: value } });
  }

  patchDomainColor(domainId: string, color: string): void {
    const profile = this.draftProfile();
    this.schedule({
      ...profile,
      domainColorOverrides: { ...profile.domainColorOverrides, [domainId]: color },
    });
  }

  clearDomainColor(domainId: string): void {
    const profile = this.draftProfile();
    const overrides = { ...profile.domainColorOverrides };
    delete overrides[domainId];
    this.schedule({ ...profile, domainColorOverrides: overrides });
  }

  private draftProfile(): GraphVisualProfile {
    return this.pendingProfile ?? this.profile();
  }

  private discardPendingProfile(): void {
    this.cancelScheduledFrame();
    this.pendingProfile = null;
  }

  private flushPendingProfile(): void {
    const pending = this.pendingProfile;
    this.cancelScheduledFrame();
    this.pendingProfile = null;
    if (pending) this.facade.activate(pending);
  }

  private schedule(profile: GraphVisualProfile): void {
    this.pendingProfile = profile;
    if (this.frameHandle !== null) return;
    this.frameUsesTimeout = typeof requestAnimationFrame !== 'function';
    const scheduleFrame = !this.frameUsesTimeout
      ? requestAnimationFrame
      : (callback: FrameRequestCallback) => setTimeout(() => callback(performance.now()), 0) as unknown as number;
    this.frameHandle = scheduleFrame(() => {
      this.frameHandle = null;
      this.frameUsesTimeout = false;
      const pending = this.pendingProfile;
      this.pendingProfile = null;
      if (!pending) return;
      this.facade.activate(pending);
    });
  }
}
