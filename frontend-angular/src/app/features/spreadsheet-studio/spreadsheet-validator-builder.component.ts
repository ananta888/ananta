import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';

type ValidatorKind = 'equals' | 'number_tolerance' | 'formula_present' | 'sum_range' | 'range_rule' | 'reference_range';

@Component({
  selector: 'app-spreadsheet-validator-builder',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <fieldset>
      <legend>Validator-Builder ohne JSON</legend>
      <p>Gebunden an v{{ documentVersion }}, {{ sheetId }}!{{ start }}:{{ end }} · <code>{{ snapshotDigest }}</code></p>
      <div class="fields">
        <label>Regel
          <select [(ngModel)]="kind">
            <option value="equals">Exakter Wert</option>
            <option value="number_tolerance">Wert mit Toleranz</option>
            <option value="formula_present">Formel vorhanden</option>
            <option value="sum_range">Bereichssumme</option>
            <option value="range_rule">Bereichsregel</option>
            <option value="reference_range">Referenzdatei-Bereich</option>
          </select>
        </label>
        @if (kind === 'equals') {
          <label>Erwarteter Wert <input [(ngModel)]="expected" maxlength="16384" /></label>
        }
        @if (kind === 'number_tolerance' || kind === 'sum_range') {
          <label>Erwartete Zahl <input type="number" [(ngModel)]="expectedNumber" /></label>
          <label>Absolute Toleranz <input type="number" min="0" [(ngModel)]="absoluteTolerance" /></label>
          <label>Relative Toleranz <input type="number" min="0" max="1" step="0.001" [(ngModel)]="relativeTolerance" /></label>
        }
        @if (kind === 'range_rule') {
          <label>Werttyp
            <select [(ngModel)]="valueType"><option value="any">Beliebig</option><option value="number">Zahl</option><option value="string">Text</option><option value="boolean">Boolesch</option></select>
          </label>
          <label><input class="check" type="checkbox" [(ngModel)]="allowEmpty" /> Leere Zellen zulassen</label>
        }
        @if (kind === 'reference_range') {
          <label>Referenz-ID <input [(ngModel)]="referenceId" maxlength="128" /></label>
          <label>Referenz-Blatt-ID <input [(ngModel)]="referenceSheetId" maxlength="128" /></label>
          <label>Referenz-Start <input [(ngModel)]="referenceStart" maxlength="10" /></label>
          <label>Referenz-Ende <input [(ngModel)]="referenceEnd" maxlength="10" /></label>
          <label><input class="check" type="checkbox" [(ngModel)]="compareFormulas" /> Formeln vergleichen</label>
        }
        <button type="button" (click)="build()" [disabled]="!valid()">Validator übernehmen</button>
      </div>
      @if (error) { <p class="error" role="alert">{{ error }}</p> }
    </fieldset>
  `,
  styles: [`
    fieldset { border:1px solid var(--border-color, #778); margin:12px 0; }
    fieldset p { overflow-wrap:anywhere; }
    .fields { display:flex; align-items:end; gap:8px; flex-wrap:wrap; }
    label { display:grid; gap:3px; min-width:150px; flex:1; }
    .check { width:auto; }
    .error { color:var(--danger, #b42318); }
  `],
})
export class SpreadsheetValidatorBuilderComponent {
  @Input({ required: true }) sheetId = '';
  @Input({ required: true }) start = 'A1';
  @Input({ required: true }) end = 'A1';
  @Input({ required: true }) documentVersion = 0;
  @Input({ required: true }) snapshotDigest = '';
  @Output() validatorChange = new EventEmitter<Record<string, unknown>>();

  kind: ValidatorKind = 'equals';
  expected = '';
  expectedNumber = 0;
  absoluteTolerance = 0;
  relativeTolerance = 0;
  valueType: 'any' | 'number' | 'string' | 'boolean' = 'any';
  allowEmpty = false;
  referenceId = '';
  referenceSheetId = '';
  referenceStart = 'A1';
  referenceEnd = 'A1';
  compareFormulas = false;
  error = '';

  valid(): boolean {
    if (!this.validCell(this.start) || !this.validCell(this.end) || !this.sheetId || !this.snapshotDigest) return false;
    if (this.kind === 'reference_range') {
      return this.validId(this.referenceId) && this.validId(this.referenceSheetId)
        && this.validCell(this.referenceStart) && this.validCell(this.referenceEnd);
    }
    return Number.isFinite(Number(this.expectedNumber))
      && Number(this.absoluteTolerance) >= 0
      && Number(this.relativeTolerance) >= 0
      && Number(this.relativeTolerance) <= 1;
  }

  build(): void {
    if (!this.valid()) { this.error = 'Die Validatorfelder sind unvollständig oder ungültig.'; return; }
    const base = {
      validator_id: `ui-validator-${crypto.randomUUID()}`,
      kind: this.kind,
      sheet_id: this.sheetId,
    };
    let validator: Record<string, unknown>;
    if (this.kind === 'equals') {
      const expected = this.expected.trim() !== '' && Number.isFinite(Number(this.expected))
        ? Number(this.expected)
        : this.expected;
      validator = { ...base, cell: this.start, expected, minimum: null, maximum: null };
    } else if (this.kind === 'formula_present') {
      validator = { ...base, cell: this.start, expected: null, minimum: null, maximum: null };
    } else if (this.kind === 'number_tolerance') {
      validator = {
        ...base, cell: this.start, expected: Number(this.expectedNumber),
        absolute_tolerance: Number(this.absoluteTolerance), relative_tolerance: Number(this.relativeTolerance),
        rounding_digits: 6,
      };
    } else if (this.kind === 'sum_range') {
      validator = {
        ...base, start: this.start, end: this.end, expected: Number(this.expectedNumber),
        absolute_tolerance: Number(this.absoluteTolerance), relative_tolerance: Number(this.relativeTolerance),
        rounding_digits: 6,
      };
    } else if (this.kind === 'range_rule') {
      validator = {
        ...base, start: this.start, end: this.end, value_type: this.valueType,
        allow_empty: this.allowEmpty, minimum: null, maximum: null,
      };
    } else {
      validator = {
        ...base,
        start: this.start,
        end: this.end,
        reference_id: this.referenceId.trim(),
        reference_sheet_id: this.referenceSheetId.trim(),
        reference_start: this.referenceStart.trim().toUpperCase(),
        reference_end: this.referenceEnd.trim().toUpperCase(),
        absolute_tolerance: Number(this.absoluteTolerance),
        relative_tolerance: Number(this.relativeTolerance),
        compare_formulas: this.compareFormulas,
      };
    }
    this.error = '';
    this.validatorChange.emit(validator);
  }

  private validCell(value: string): boolean { return /^[A-Z]{1,3}[1-9][0-9]{0,6}$/.test(value.trim().toUpperCase()); }
  private validId(value: string): boolean { return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value.trim()); }
}
