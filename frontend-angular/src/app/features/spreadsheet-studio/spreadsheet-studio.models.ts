export interface SpreadsheetCell {
  address: string;
  value: string | number | boolean | null;
  formula: Record<string, unknown> | null;
  style_ref: string | null;
}

export interface WorkbookSnapshot {
  schema: 'ananta.spreadsheet-workbook-snapshot.v1';
  snapshot_id: string;
  document_version_id: string;
  sheets: Array<{ sheet_id: string; name: string; hidden: boolean; cells: SpreadsheetCell[] }>;
}

export interface SpreadsheetDocument {
  schema: 'ananta.spreadsheet-document-version.v1';
  document_id: string;
  title: string;
  version: number;
  snapshot: WorkbookSnapshot;
  snapshot_digest: string;
  state: 'published';
  source_grounding_verified: false;
  human_intervention_required: false;
}

export interface SpreadsheetProposalResult {
  proposal_id: string;
  state: 'promoted' | 'candidate_ready' | 'rejected';
  promoted_version: number | null;
  diff: Array<{ sheet_id: string; cell: string; before: SpreadsheetCell | null; after: SpreadsheetCell | null }>;
  reason_codes: string[];
  human_intervention_required: false;
}
