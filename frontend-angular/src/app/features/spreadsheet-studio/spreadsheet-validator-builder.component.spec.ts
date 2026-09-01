import { TestBed } from '@angular/core/testing';

import { SpreadsheetValidatorBuilderComponent } from './spreadsheet-validator-builder.component';

describe('SpreadsheetValidatorBuilderComponent', () => {
  it('builds a closed version-bound range rule without free-form JSON', () => {
    TestBed.configureTestingModule({ imports: [SpreadsheetValidatorBuilderComponent] });
    const fixture = TestBed.createComponent(SpreadsheetValidatorBuilderComponent);
    const component = fixture.componentInstance;
    component.sheetId = 'sheet-one';
    component.start = 'A1';
    component.end = 'A8';
    component.documentVersion = 4;
    component.snapshotDigest = 'a'.repeat(64);
    component.kind = 'sum_range';
    component.expectedNumber = 42;
    component.absoluteTolerance = 0.01;
    const emitted = vi.fn();
    component.validatorChange.subscribe(emitted);

    component.build();

    expect(emitted).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'sum_range', sheet_id: 'sheet-one', start: 'A1', end: 'A8', expected: 42,
    }));
    expect(emitted.mock.calls[0][0]).not.toHaveProperty('json');
  });
});
