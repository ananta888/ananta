import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { ExplanationNoticeComponent } from '../../shared/ui/display';
import { PageIntroComponent, SectionCardComponent } from '../../shared/ui/layout';
import { SpreadsheetStudioApiService } from './spreadsheet-studio-api.service';
import { SpreadsheetDocument, WorkbookSnapshot } from './spreadsheet-studio.models';

@Component({
  selector: 'app-spreadsheet-studio-page',
  standalone: true,
  imports: [ExplanationNoticeComponent, FormsModule, PageIntroComponent, SectionCardComponent],
  template: `
    <app-page-intro
      title="Spreadsheet Studio"
      subtitle="Geschlossene Zellaktionen, deterministischer Dry-run und Hub-Policy-Promotion." />
    <app-explanation-notice
      title="Experimenteller Mock-Slice"
      message="Keine LibreOffice-Fidelity, kein XLSX/ODS-Apply und keine Grounding-Claims. Der automatische Pfad ist vollständig headless, aber nicht produktionsbereit."
      tone="technical" />
    <div class="grid">
      <app-section-card title="Dokumente" subtitle="Immutable, tenantgebundene Workbook-Snapshots.">
        <div class="row"><input [(ngModel)]="title" maxlength="200" placeholder="Titel" />
          <button type="button" (click)="create()" [disabled]="busy || !title.trim()">Mock-Workbook anlegen</button></div>
        @for (document of documents; track document.document_id) {
          <button type="button" class="entry" (click)="select(document)">
            {{ document.title }} · v{{ document.version }}
          </button>
        }
      </app-section-card>
      <app-section-card title="Zellaktion" subtitle="Automatic policy path; keine UI-Bestätigung erforderlich.">
        @if (selected) {
          <p>{{ selected.title }} · Basis {{ selected.snapshot_digest }}</p>
          <div class="row">
            <input [(ngModel)]="cell" maxlength="10" aria-label="Zelladresse" />
            <input [(ngModel)]="value" maxlength="16384" aria-label="Neuer Wert" />
            <button type="button" (click)="execute()" [disabled]="busy || !validCell()">Ausführen</button>
          </div>
          <div class="cells">
            @for (item of selected.snapshot.sheets[0].cells; track item.address) {
              <span>{{ item.address }}</span><strong>{{ item.value }}</strong>
            }
          </div>
        } @else { <p>Dokument auswählen.</p> }
        @if (message) { <p>{{ message }}</p> }
        @if (error) { <p class="error" role="alert">{{ error }}</p> }
      </app-section-card>
    </div>
  `,
  styles: [`
    .grid { display:grid; grid-template-columns:1fr 2fr; gap:16px; margin-top:16px; }
    .row { display:flex; gap:8px; flex-wrap:wrap; } input { min-width:130px; flex:1; }
    .entry { display:block; width:100%; margin:6px 0; text-align:left; }
    .cells { display:grid; grid-template-columns:auto 1fr; gap:6px 14px; margin-top:14px; }
    .error { color:var(--danger); overflow-wrap:anywhere; }
    @media(max-width:760px){.grid{grid-template-columns:1fr;}}
  `],
})
export class SpreadsheetStudioPageComponent implements OnInit {
  private readonly api = inject(SpreadsheetStudioApiService);
  private readonly agents = inject(AgentDirectoryService);
  documents: SpreadsheetDocument[] = [];
  selected: SpreadsheetDocument | null = null;
  title = 'Automatic Workbook';
  cell = 'A1';
  value = '42';
  busy = false;
  message = '';
  error = '';

  ngOnInit(): void { this.load(); }

  load(): void {
    const hub = this.hubUrl();
    if (!hub) { this.error = 'Kein Hub konfiguriert.'; return; }
    this.api.list(hub).subscribe({
      next: page => { this.documents = page.items; },
      error: error => this.fail(error),
    });
  }

  create(): void {
    const hub = this.hubUrl();
    if (!hub || !this.title.trim()) return;
    this.busy = true;
    const nonce = crypto.randomUUID();
    const snapshot: WorkbookSnapshot = {
      schema: 'ananta.spreadsheet-workbook-snapshot.v1',
      snapshot_id: `snapshot-${nonce}`,
      document_version_id: `document-version-${nonce}`,
      sheets: [{
        sheet_id: 'sheet-one', name: 'Sheet 1', hidden: false,
        cells: [{ address: 'A1', value: 1, formula: null, style_ref: null }],
      }],
    };
    this.api.create(hub, this.title.trim(), snapshot).subscribe({
      next: document => {
        this.busy = false;
        this.documents = [...this.documents, document];
        this.select(document);
      },
      error: error => this.fail(error),
    });
  }

  select(document: SpreadsheetDocument): void {
    this.selected = document;
    this.message = '';
    this.error = '';
  }

  validCell(): boolean { return /^[A-Z]{1,3}[1-9][0-9]{0,6}$/.test(this.cell.trim().toUpperCase()); }

  execute(): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected || !this.validCell()) return;
    this.busy = true;
    const actionValue = Number.isFinite(Number(this.value)) && this.value.trim() !== '' ? Number(this.value) : this.value;
    const proposal = {
      schema: 'ananta.spreadsheet-proposal.v1', proposal_id: `proposal-${crypto.randomUUID()}`,
      document_id: this.selected.document_id, expected_version: this.selected.version,
      base_snapshot_digest: this.selected.snapshot_digest,
      actions: [{ action_id: 'ui-action', kind: 'set_value', sheet_id: 'sheet-one',
        cell: this.cell.trim().toUpperCase(), value: actionValue, formula: null }],
      validators: [{ validator_id: 'ui-validator', kind: 'equals', sheet_id: 'sheet-one',
        cell: this.cell.trim().toUpperCase(), expected: actionValue, minimum: null, maximum: null }],
      automatic_promotion: true,
    };
    this.api.execute(hub, proposal).subscribe({
      next: result => {
        this.busy = false;
        this.message = result.state === 'promoted'
          ? `Automatisch als Version ${result.promoted_version} promoviert.`
          : result.reason_codes.join(', ');
        this.load();
      },
      error: error => this.fail(error),
    });
  }

  private hubUrl(): string { return this.agents.list().find(agent => agent.role === 'hub')?.url || ''; }

  private fail(error: unknown): void {
    this.busy = false;
    const response = error as { error?: { message?: string }; message?: string };
    this.error = response.error?.message || response.message || 'Spreadsheet-Anfrage fehlgeschlagen.';
  }
}
