import { TestBed } from '@angular/core/testing';

import { SpreadsheetProposalReviewComponent } from './spreadsheet-proposal-review.component';
import { SpreadsheetDocument, SpreadsheetProposalResult } from './spreadsheet-studio.models';

describe('SpreadsheetProposalReviewComponent', () => {
  const document = {
    document_id: 'document-one', version: 2, snapshot_digest: 'a'.repeat(64), unsupported_objects: [],
  } as SpreadsheetDocument;
  const proposal = {
    document_id: 'document-one', base_version: 2, base_snapshot_digest: 'a'.repeat(64),
    state: 'candidate_ready', validation: { passed: true, reason_codes: [], results: [] },
    actual_diff: { items: [], total: 0, has_more: false, diff_digest: 'b'.repeat(64) },
  } as unknown as SpreadsheetProposalResult;

  it('allows apply only for the synchronized current version without unsupported objects', () => {
    TestBed.configureTestingModule({ imports: [SpreadsheetProposalReviewComponent] });
    const component = TestBed.createComponent(SpreadsheetProposalReviewComponent).componentInstance;
    component.document = document;
    component.proposal = proposal;
    component.synchronized = true;

    expect(component.canApply()).toBe(true);
    component.document = { ...document, version: 3 };
    expect(component.canApply()).toBe(false);
    component.document = document;
    component.unsupportedObjects = ['macro'];
    expect(component.canApply()).toBe(false);
  });
});
