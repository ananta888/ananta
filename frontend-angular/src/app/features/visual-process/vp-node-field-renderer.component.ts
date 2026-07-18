import { ChangeDetectionStrategy, Component, EventEmitter, Input, OnChanges, Output, SimpleChanges } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ArtifactRef, ValidationIssue } from './visual-process-api.service';
import { VpNodeFieldDefinition, VpNodeFieldOption } from './vp-node-definition-registry.service';
import { VpResourceOptionSnapshot } from './vp-resource-option-provider';

/** One schema field renderer. It owns presentation mechanics, never graph state or orchestration. */
@Component({
  selector: 'app-vp-node-field-renderer',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrls: ['./visual-process-editor.component.scss'],
  template: `
    <div class="vpe-schema-field" [attr.data-field-path]="field.path"
         [class.has-error]="issues.length > 0" [class.is-deprecated]="field.deprecated">
      @switch (field.type) {
        @case ('boolean') {
          <label class="vpe-label vpe-checkbox">
            <input type="checkbox" [ngModel]="!!value" [disabled]="field.readOnly"
                   (ngModelChange)="emitValue($event)" /> {{ field.label }}{{ required ? ' *' : '' }}
          </label>
        }
        @case ('number') {
          <label class="vpe-label">{{ field.label }}{{ required ? ' *' : '' }}
            <input class="vpe-input" type="number" [min]="field.min ?? null" [max]="field.max ?? null"
                   [step]="field.step ?? 'any'" [ngModel]="value" [disabled]="field.readOnly"
                   (ngModelChange)="emitValue($event === null || $event === '' ? undefined : +$event)" />
          </label>
        }
        @case ('enum') {
          <label class="vpe-label">{{ field.label }}{{ required ? ' *' : '' }}
            <select class="vpe-input" [ngModel]="value" [disabled]="field.readOnly" (ngModelChange)="emitValue($event)">
              @if (!required) { <option value="">— Standard —</option> }
              @for (option of options; track option.value) { <option [ngValue]="option.value">{{ option.label }}</option> }
            </select>
          </label>
        }
        @case ('resource-reference') {
          @if (options.length || usesOptionProvider) {
            <label class="vpe-label">{{ field.label }}{{ required ? ' *' : '' }}
              <select class="vpe-input" [ngModel]="value"
                      [disabled]="field.readOnly || optionState?.status === 'loading' || optionState?.status === 'failed'"
                      (ngModelChange)="emitValue($event)">
                <option value="">— nicht gesetzt —</option>
                @for (option of options; track option.value) {
                  <option [ngValue]="option.value" [disabled]="option.disabled">{{ option.label }}</option>
                }
              </select>
            </label>
            @if (optionState && optionState.status !== 'ready') {
              <div class="vpe-field-help" [class.vpe-field-error]="optionState.status === 'failed'">
                {{ resourceStatusLabel(optionState) }}
                @if (optionState.status === 'degraded' || optionState.status === 'failed') {
                  <button type="button" class="vpe-btn-xs" (click)="refreshRequested.emit()">Neu laden</button>
                }
              </div>
            }
          } @else {
            <label class="vpe-label">{{ field.label }}{{ required ? ' *' : '' }}
              <input class="vpe-input" type="text" [ngModel]="value" [disabled]="field.readOnly"
                     (ngModelChange)="emitValue($event)" />
            </label>
          }
        }
        @case ('multi-select') {
          <fieldset class="vpe-fieldset"><legend>{{ field.label }}</legend>
            @for (option of options; track option.value) {
              <label class="vpe-label vpe-checkbox"><input type="checkbox" [disabled]="field.readOnly"
                [checked]="isOptionSelected(option.value)"
                (change)="toggleOption(option.value, $any($event.target).checked)" /> {{ option.label }}</label>
            }
          </fieldset>
        }
        @case ('structured-list') {
          <label class="vpe-label">{{ field.label }}{{ required ? ' *' : '' }}
            <textarea class="vpe-input vpe-structured-input" rows="4" [disabled]="field.readOnly"
                      [ngModel]="structuredText" (ngModelChange)="setStructuredDraft($event)"
                      (blur)="commitStructured()"></textarea>
          </label>
          @if (structuredError) { <div class="vpe-field-error" role="alert">{{ structuredError }}</div> }
        }
        @case ('io-port') {
          <fieldset class="vpe-fieldset"><legend>{{ field.label }}</legend>
            @for (port of ports(); track $index) {
              <div class="vpe-io-field-row">
                <input class="vpe-input" aria-label="Portname" [ngModel]="port.name" [disabled]="field.readOnly"
                       (ngModelChange)="setPortValue($index, 'name', $event)" />
                <select class="vpe-input" aria-label="Artefakttyp" [ngModel]="port.kind" [disabled]="field.readOnly"
                        (ngModelChange)="setPortValue($index, 'kind', $event)">
                  @for (kind of artifactKinds; track kind) { <option [value]="kind">{{ kind }}</option> }
                </select>
                <label class="vpe-checkbox"><input type="checkbox" [ngModel]="port.required" [disabled]="field.readOnly"
                  (ngModelChange)="setPortValue($index, 'required', $event)" /> Pflicht</label>
                <button type="button" class="vpe-btn-xs" aria-label="Port entfernen" [disabled]="field.readOnly"
                        (click)="removePort($index)">×</button>
              </div>
            }
            <button type="button" class="vpe-btn-xs" [disabled]="field.readOnly" (click)="addPort()">Port hinzufügen</button>
          </fieldset>
        }
        @case ('textarea') {
          <label class="vpe-label">{{ field.label }}{{ required ? ' *' : '' }}
            <textarea class="vpe-input" rows="2" [ngModel]="value" [disabled]="field.readOnly"
                      (ngModelChange)="emitValue($event)"></textarea>
          </label>
        }
        @default {
          <label class="vpe-label">{{ field.label }}{{ required ? ' *' : '' }}
            <input class="vpe-input" [type]="field.type === 'secret-reference' ? 'password' : 'text'"
                   [ngModel]="value" [disabled]="field.readOnly" (ngModelChange)="emitValue($event)" />
          </label>
        }
      }
      <div class="vpe-field-help">{{ field.description }}
        @if (field.effect && field.effect !== field.description) { <span> Wirkung: {{ field.effect }}</span> }
        @if (field.default !== undefined) { <span> Standard: <code>{{ field.default }}</code>.</span> }
        @if (field.example !== undefined) { <span> Beispiel: <code>{{ exampleText() }}</code>.</span> }
        @if (required) { <span> Pflichtfeld.</span> }
        @if (field.deprecated) { <span> Veraltet; nur zur Migration vorhanden.</span> }
        @if (field.readOnly) { <span> Nur lesbar.</span> }
      </div>
      @for (issue of issues; track issue.code + ':' + issue.path) {
        <div class="vpe-field-error" role="alert">{{ issue.message }}</div>
      }
    </div>
  `,
})
export class VpNodeFieldRendererComponent implements OnChanges {
  @Input({ required: true }) field!: VpNodeFieldDefinition;
  @Input() value: unknown = null;
  @Input() required = false;
  @Input() options: readonly VpNodeFieldOption[] = [];
  @Input() optionState: VpResourceOptionSnapshot | null = null;
  @Input() usesOptionProvider = false;
  @Input() issues: readonly ValidationIssue[] = [];
  @Input() artifactKinds: readonly string[] = [];
  @Output() valueChanged = new EventEmitter<unknown>();
  @Output() refreshRequested = new EventEmitter<void>();
  structuredText = '[]';
  structuredError = '';

  ngOnChanges(changes: SimpleChanges): void {
    if ((changes['field'] || changes['value']) && this.field?.type === 'structured-list') {
      try { this.structuredText = JSON.stringify(this.value ?? [], null, 2); }
      catch { this.structuredText = '[]'; }
      this.structuredError = '';
    }
  }

  emitValue(value: unknown): void { if (!this.field.readOnly) this.valueChanged.emit(value); }

  isOptionSelected(value: unknown): boolean { return Array.isArray(this.value) && this.value.includes(value); }

  toggleOption(value: unknown, checked: boolean): void {
    const current = Array.isArray(this.value) ? [...this.value] : [];
    this.emitValue(checked ? [...new Set([...current, value])] : current.filter(item => item !== value));
  }

  setStructuredDraft(value: string): void { this.structuredText = value; this.structuredError = ''; }

  commitStructured(): void {
    try {
      const parsed: unknown = JSON.parse(this.structuredText);
      if (!Array.isArray(parsed)) throw new Error('structured_list_array_required');
      this.emitValue(parsed);
    } catch {
      this.structuredError = 'Gültiges JSON-Array erforderlich.';
    }
  }

  ports(): ArtifactRef[] { return Array.isArray(this.value) ? this.value as ArtifactRef[] : []; }

  addPort(): void { this.emitValue([...this.ports(), { name: 'artifact', kind: 'text', required: false }]); }

  setPortValue(index: number, key: keyof ArtifactRef, value: unknown): void {
    const ports = this.ports().map(port => ({ ...port }));
    if (!ports[index]) return;
    (ports[index] as unknown as Record<string, unknown>)[key] = value;
    this.emitValue(ports);
  }

  removePort(index: number): void { this.emitValue(this.ports().filter((_port, candidate) => candidate !== index)); }

  exampleText(): string {
    if (typeof this.field.example === 'string') return this.field.example;
    try { return JSON.stringify(this.field.example); } catch { return String(this.field.example ?? ''); }
  }

  resourceStatusLabel(state: VpResourceOptionSnapshot): string {
    if (state.status === 'loading') return 'Optionen werden geladen …';
    if (state.status === 'empty') return 'Keine autorisierten Optionen verfügbar.';
    if (state.status === 'degraded') return `Optionen sind veraltet (${state.reason ?? 'degraded'}).`;
    if (state.status === 'failed') return `Optionen konnten nicht geladen werden (${state.reason ?? 'failed'}).`;
    return '';
  }
}
