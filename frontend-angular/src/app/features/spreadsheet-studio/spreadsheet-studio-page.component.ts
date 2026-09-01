import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { ExplanationNoticeComponent } from '../../shared/ui/display';
import { PageIntroComponent, SectionCardComponent } from '../../shared/ui/layout';
import { SpreadsheetStudioApiService } from './spreadsheet-studio-api.service';
import {
  SpreadsheetDataset,
  SpreadsheetDocument,
  SpreadsheetFeedbackEvent,
  SpreadsheetInferenceProposal,
  SpreadsheetPrivacyPreview,
  SpreadsheetProposalResult,
  SpreadsheetStudioCapabilities,
  SpreadsheetTrainingAdmission,
  SpreadsheetTrainingConsent,
  WorkbookSnapshot,
} from './spreadsheet-studio.models';

@Component({
  selector: 'app-spreadsheet-studio-page',
  standalone: true,
  imports: [CommonModule, ExplanationNoticeComponent, FormsModule, PageIntroComponent, SectionCardComponent],
  template: `
    <app-page-intro
      title="Spreadsheet Studio"
      subtitle="LibreOffice-isolierte Tabellenaktionen, automatische Validierung und einwilligungsgebundenes LoRA-Feedback." />
    @if (capabilities) {
      <app-explanation-notice
        [title]="capabilities.available ? 'Automatischer Ausführungspfad aktiv' : 'Spreadsheet Studio deaktiviert'"
        [message]="capabilityMessage()"
        [tone]="capabilities.available ? 'technical' : 'warning'" />
    }

    <div class="grid">
      <app-section-card title="Arbeitsmappen" subtitle="XLSX, ODS und CSV werden unverändert archiviert und isoliert analysiert.">
        <label for="spreadsheet-title">Titel</label>
        <input id="spreadsheet-title" [(ngModel)]="title" maxlength="200" placeholder="Titel der Arbeitsmappe" />
        <div class="row">
          <label class="file-button" for="spreadsheet-file">Datei auswählen</label>
          <input id="spreadsheet-file" class="file-input" type="file" accept=".xlsx,.ods,.csv" (change)="selectFile($event)" />
          <span>{{ upload?.name || 'Keine Datei gewählt' }}</span>
          <button type="button" (click)="importDocument()" [disabled]="busy || !upload">Importieren</button>
          <button type="button" class="secondary" (click)="createCanonical()" [disabled]="busy || !title.trim()">Leere Mappe</button>
        </div>
        @for (document of documents; track document.document_id) {
          <button type="button" class="entry" [class.selected]="selected?.document_id === document.document_id" (click)="select(document)">
            <strong>{{ document.title }}</strong>
            <span>v{{ document.version }} · {{ document.source_artifact?.format?.toUpperCase() || 'kanonisch' }}</span>
          </button>
        } @empty { <p>Noch keine Arbeitsmappe vorhanden.</p> }
      </app-section-card>

      <app-section-card title="Vorschau und Aktion" subtitle="Dry-run, Validatoren und Promotion laufen ohne menschlichen Freigabeschritt.">
        @if (selected) {
          <div class="document-meta">
            <strong>{{ selected.title }} · Version {{ selected.version }}</strong>
            <code>{{ selected.snapshot_digest }}</code>
            @if (selected.source_artifact) {
              <button type="button" class="secondary" (click)="downloadOriginal()" [disabled]="busy">Original herunterladen</button>
            }
          </div>
          <div class="row">
            <label>Zelle <input [(ngModel)]="cell" maxlength="10" aria-label="Zelladresse" /></label>
            <label>Wert <input [(ngModel)]="value" maxlength="16384" aria-label="Neuer Wert" /></label>
            <button type="button" (click)="execute()" [disabled]="busy || !validCell()">Validieren und ausführen</button>
          </div>
          @for (sheet of selected.snapshot.sheets; track sheet.sheet_id) {
            <details [open]="$index === 0">
              <summary>{{ sheet.name }} · {{ sheet.cells.length }} belegte Zellen</summary>
              <div class="cells">
                @for (item of sheet.cells.slice(0, 200); track item.address) {
                  <span>{{ item.address }}</span><strong>{{ item.formula ? formulaText(item.formula) : item.value }}</strong>
                }
              </div>
            </details>
          }
        } @else { <p>Arbeitsmappe auswählen oder importieren.</p> }
        @if (lastProposal) {
          <div class="result" aria-live="polite">
            <strong>{{ lastProposal.state }} · {{ lastProposal.diff.length }} Änderungen</strong>
            <span>{{ lastProposal.reason_codes.join(', ') || 'Alle automatischen Prüfungen bestanden.' }}</span>
          </div>
        }
      </app-section-card>

      <app-section-card title="Feedback und Datenschutz" subtitle="Nur maskierte, explizit eingewilligte Beispiele gelangen in Datensätze.">
        <label for="feedback-instruction">Aufgabenbeschreibung</label>
        <textarea id="feedback-instruction" [(ngModel)]="instruction" maxlength="4000" rows="3"></textarea>
        <div class="row">
          <button type="button" (click)="recordFeedback('accepted')" [disabled]="busy || !lastProposal || !instruction.trim()">Ergebnis akzeptieren</button>
          <button type="button" class="secondary" (click)="recordFeedback('rejected')" [disabled]="busy || !lastProposal || !instruction.trim()">Ablehnen</button>
        </div>
        @if (privacyPreview) {
          <details open><summary>Maskierte Trainingsvorschau · {{ privacyPreview.masking_version }}</summary>
            <pre>{{ privacyPreview.record | json }}</pre>
          </details>
          <label>Aufbewahrung in Tagen <input type="number" min="1" max="3650" [(ngModel)]="retentionDays" /></label>
          <button type="button" (click)="grantConsent()" [disabled]="busy || !!consent">Einwilligen</button>
        }
        @if (consent) {
          <p>Einwilligung {{ consent.state }} · Version {{ consent.version }}</p>
          @if (consent.state === 'active') {
            <button type="button" class="secondary" (click)="revokeConsent()" [disabled]="busy">Einwilligung widerrufen</button>
          }
        }
      </app-section-card>

      <app-section-card title="ML-Intern" subtitle="Reproduzierbarer Datensatz, Dry-run-Training und strikt nicht automatisch angewandte Inferenz.">
        <div class="row">
          <button type="button" (click)="materializeDataset()" [disabled]="busy || consent?.state !== 'active' || feedback?.kind === 'rejected'">Datensatz materialisieren</button>
          <button type="button" (click)="startDryRunTraining()" [disabled]="busy || !dataset?.readiness?.dry_run_ready">Training-Dry-run starten</button>
        </div>
        @if (dataset) {
          <p>{{ dataset.record_count }} Beispiele · {{ dataset.dataset_digest }}</p>
          @if (!dataset.readiness.training_ready) { <p>{{ dataset.readiness.reason_codes.join(', ') }}</p> }
        }
        @if (training) {
          <p>ML-Intern-Job {{ training.job['id'] || training.job['job_id'] }} · {{ training.job['state'] }}</p>
        }
        <hr />
        <div class="fields">
          <label>Adapter-ID <input [(ngModel)]="adapterId" /></label>
          <label>Adapter-Version <input [(ngModel)]="adapterVersion" /></label>
          <label>Basismodell <input [(ngModel)]="baseModel" /></label>
        </div>
        <button type="button" (click)="infer()" [disabled]="busy || !selected || !inferenceReady()">LoRA-Vorschlag erzeugen</button>
        @if (inferenceProposal) {
          <pre>{{ inferenceProposal.result.actions | json }}</pre>
          <button type="button" (click)="applyInferenceProposal()" [disabled]="busy">Vorschlag automatisch prüfen und ausführen</button>
        }
      </app-section-card>
    </div>
    @if (message) { <p class="notice" aria-live="polite">{{ message }}</p> }
    @if (error) { <p class="error" role="alert">{{ error }}</p> }
  `,
  styles: [`
    .grid { display:grid; grid-template-columns:minmax(280px, 1fr) minmax(420px, 2fr); gap:16px; margin-top:16px; }
    .row,.fields { display:flex; align-items:end; gap:8px; flex-wrap:wrap; margin:10px 0; }
    .fields label,.row label { flex:1; min-width:130px; }
    input,textarea { box-sizing:border-box; width:100%; }
    textarea { resize:vertical; }
    .file-input { position:absolute; inline-size:1px; block-size:1px; opacity:0; }
    .file-button { padding:8px 12px; border:1px solid var(--border-color, #667); border-radius:4px; cursor:pointer; }
    .entry { display:flex; justify-content:space-between; width:100%; margin:6px 0; text-align:left; gap:8px; }
    .entry.selected { outline:2px solid var(--primary, #5877e8); }
    .document-meta,.result { display:flex; flex-direction:column; gap:5px; margin-bottom:12px; }
    code,pre { overflow:auto; overflow-wrap:anywhere; white-space:pre-wrap; }
    details { margin-top:12px; }
    .cells { display:grid; grid-template-columns:auto 1fr; gap:6px 14px; margin-top:10px; max-height:360px; overflow:auto; }
    .secondary { opacity:.85; }
    .notice { padding:10px; border-left:3px solid var(--primary, #5877e8); }
    .error { color:var(--danger, #c33); overflow-wrap:anywhere; }
    hr { margin:16px 0; opacity:.35; }
    @media(max-width:860px){.grid{grid-template-columns:1fr;}}
  `],
})
export class SpreadsheetStudioPageComponent implements OnInit {
  private readonly api = inject(SpreadsheetStudioApiService);
  private readonly agents = inject(AgentDirectoryService);

  capabilities: SpreadsheetStudioCapabilities | null = null;
  documents: SpreadsheetDocument[] = [];
  selected: SpreadsheetDocument | null = null;
  upload: File | null = null;
  lastProposal: SpreadsheetProposalResult | null = null;
  feedback: SpreadsheetFeedbackEvent | null = null;
  privacyPreview: SpreadsheetPrivacyPreview | null = null;
  consent: SpreadsheetTrainingConsent | null = null;
  dataset: SpreadsheetDataset | null = null;
  inferenceProposal: SpreadsheetInferenceProposal | null = null;
  training: SpreadsheetTrainingAdmission | null = null;
  title = 'Automatic Workbook';
  cell = 'A1';
  value = '42';
  instruction = 'Setze den gewünschten Tabellenwert korrekt.';
  retentionDays = 365;
  adapterId = '';
  adapterVersion = '';
  baseModel = '';
  busy = false;
  message = '';
  error = '';

  ngOnInit(): void {
    const hub = this.hubUrl();
    if (!hub) { this.error = 'Kein Hub konfiguriert.'; return; }
    this.api.capabilities(hub).subscribe({ next: value => this.capabilities = value, error: error => this.fail(error) });
    this.load();
  }

  capabilityMessage(): string {
    if (!this.capabilities) return '';
    if (!this.capabilities.available) return this.capabilities.reason_code || 'Der Hub hat den Dienst deaktiviert.';
    const fidelity = this.capabilities.libreoffice_fidelity_verified ? 'LibreOffice-Fidelity verifiziert' : 'kanonischer Fallback';
    return `${fidelity}; Formate: ${this.capabilities.supported_formats.join(', ')}; automatische Promotion: ${this.capabilities.automatic_promotion_enabled ? 'aktiv' : 'deaktiviert'}.`;
  }

  load(preferredId = this.selected?.document_id): void {
    const hub = this.hubUrl();
    if (!hub) return;
    this.api.list(hub).subscribe({
      next: page => {
        this.documents = page.items;
        this.selected = page.items.find(item => item.document_id === preferredId) || this.selected;
      },
      error: error => this.fail(error),
    });
  }

  selectFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.upload = input.files?.item(0) || null;
    if (this.upload && this.title === 'Automatic Workbook') this.title = this.upload.name.replace(/\.[^.]+$/, '');
  }

  importDocument(): void {
    const hub = this.hubUrl();
    if (!hub || !this.upload) return;
    this.begin();
    this.api.importDocument(hub, this.upload, this.title).subscribe({
      next: document => this.completeWithDocument(document, 'Arbeitsmappe isoliert importiert und archiviert.'),
      error: error => this.fail(error),
    });
  }

  createCanonical(): void {
    const hub = this.hubUrl();
    if (!hub || !this.title.trim()) return;
    this.begin();
    const nonce = crypto.randomUUID();
    const snapshot: WorkbookSnapshot = {
      schema: 'ananta.spreadsheet-workbook-snapshot.v1',
      snapshot_id: `snapshot-${nonce}`,
      document_version_id: `document-version-${nonce}`,
      sheets: [{ sheet_id: 'sheet-one', name: 'Sheet 1', hidden: false, cells: [] }],
    };
    this.api.create(hub, this.title.trim(), snapshot).subscribe({
      next: document => this.completeWithDocument(document, 'Kanonische Arbeitsmappe angelegt.'),
      error: error => this.fail(error),
    });
  }

  select(document: SpreadsheetDocument): void {
    this.selected = document;
    this.resetLearningState();
    this.clearStatus();
  }

  validCell(): boolean { return /^[A-Z]{1,3}[1-9][0-9]{0,6}$/.test(this.cell.trim().toUpperCase()); }

  execute(): void {
    if (!this.selected || !this.validCell()) return;
    const actionValue = Number.isFinite(Number(this.value)) && this.value.trim() !== '' ? Number(this.value) : this.value;
    this.executeActions([{
      action_id: `ui-action-${crypto.randomUUID()}`,
      kind: 'set_value',
      sheet_id: this.selected.snapshot.sheets[0]?.sheet_id,
      cell: this.cell.trim().toUpperCase(),
      value: actionValue,
      formula: null,
    }], [{
      validator_id: `ui-validator-${crypto.randomUUID()}`,
      kind: 'equals',
      sheet_id: this.selected.snapshot.sheets[0]?.sheet_id,
      cell: this.cell.trim().toUpperCase(),
      expected: actionValue,
      minimum: null,
      maximum: null,
    }]);
  }

  recordFeedback(kind: 'accepted' | 'rejected'): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected || !this.lastProposal || !this.instruction.trim()) return;
    this.begin();
    this.api.recordFeedback(hub, {
      schema: 'ananta.spreadsheet-feedback-command.v1',
      event_id: `feedback-${crypto.randomUUID()}`,
      document_id: this.selected.document_id,
      proposal_id: this.lastProposal.proposal_id,
      kind,
      instruction: this.instruction.trim(),
      correction_actions: [],
      excluded_cells: [],
    }).subscribe({
      next: feedback => {
        this.feedback = feedback;
        this.consent = null;
        this.dataset = null;
        if (feedback.kind === 'accepted') this.loadPrivacyPreview(feedback.event_id);
        else this.done('Feedback revisionssicher gespeichert.');
      },
      error: error => this.fail(error),
    });
  }

  grantConsent(): void {
    const hub = this.hubUrl();
    if (!hub || !this.feedback || !this.privacyPreview) return;
    this.begin();
    this.api.grantConsent(hub, {
      schema: 'ananta.spreadsheet-training-consent-command.v1',
      consent_id: `consent-${crypto.randomUUID()}`,
      feedback_id: this.feedback.event_id,
      record_digest: this.privacyPreview.record_digest,
      purpose: 'spreadsheet_action_training',
      retention_days: Number(this.retentionDays),
      granted: true,
    }).subscribe({
      next: consent => { this.consent = consent; this.done('Einwilligung aktiv; das Beispiel darf materialisiert werden.'); },
      error: error => this.fail(error),
    });
  }

  revokeConsent(): void {
    const hub = this.hubUrl();
    if (!hub || !this.consent) return;
    this.begin();
    this.api.revokeConsent(hub, this.consent.consent_id, this.consent.version).subscribe({
      next: consent => { this.consent = consent; this.dataset = null; this.done('Einwilligung widerrufen.'); },
      error: error => this.fail(error),
    });
  }

  materializeDataset(): void {
    const hub = this.hubUrl();
    if (!hub || !this.feedback || this.consent?.state !== 'active') return;
    this.begin();
    this.api.materializeDataset(hub, {
      schema: 'ananta.spreadsheet-dataset-command.v1',
      dataset_id: `spreadsheet-dataset-${crypto.randomUUID()}`,
      feedback_ids: [this.feedback.event_id],
      recipe_version: 'spreadsheet-recipe-v1',
      split_seed: 'spreadsheet-studio-default',
      split_percent: { train: 80, validation: 10, eval: 5, test: 5 },
    }).subscribe({
      next: dataset => { this.dataset = dataset; this.done('Einwilligungsgebundener Datensatz materialisiert.'); },
      error: error => this.fail(error),
    });
  }

  startDryRunTraining(): void {
    const hub = this.hubUrl();
    if (!hub || !this.dataset?.readiness.dry_run_ready) return;
    this.begin();
    const command = {
      schema: 'ananta.spreadsheet-training-command.v1',
      dataset_id: this.dataset.dataset_id,
      mode: 'dry_run',
      backend: 'mock',
      base_model: this.baseModel.trim() || 'spreadsheet-dry-run-model',
      method: 'lora',
      hyperparameters: {},
      live_confirmed: false,
      risk_reason: 'Automatischer, nicht publizierender Spreadsheet-Dry-run.',
    };
    this.api.startTraining(hub, this.dataset.dataset_id, command, `spreadsheet-ui-${crypto.randomUUID()}`).subscribe({
      next: training => { this.training = training; this.done('ML-Intern-Dry-run aufgenommen.'); },
      error: error => this.fail(error),
    });
  }

  inferenceReady(): boolean {
    return Boolean(this.adapterId.trim() && this.adapterVersion.trim() && this.baseModel.trim() && this.instruction.trim());
  }

  infer(): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected || !this.inferenceReady()) return;
    this.begin();
    this.api.infer(hub, {
      schema: 'ananta.spreadsheet-inference-command.v1',
      document_id: this.selected.document_id,
      instruction: this.instruction.trim(),
      adapter_id: this.adapterId.trim(),
      adapter_version: this.adapterVersion.trim(),
      base_model: this.baseModel.trim(),
      task_id: `spreadsheet-inference-${crypto.randomUUID()}`,
      max_new_tokens: 2048,
      temperature: 0,
    }).subscribe({
      next: proposal => { this.inferenceProposal = proposal; this.done('LoRA-Aktionen erzeugt; sie wurden nicht automatisch angewandt.'); },
      error: error => this.fail(error),
    });
  }

  applyInferenceProposal(): void {
    if (!this.inferenceProposal) return;
    this.executeActions(this.inferenceProposal.result.actions, []);
  }

  downloadOriginal(): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected?.source_artifact) return;
    this.begin();
    const selected = this.selected;
    this.api.downloadOriginal(hub, selected.document_id).subscribe({
      next: response => {
        const blob = response.body;
        if (!blob) { this.fail(new Error('spreadsheet_download_empty')); return; }
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `${selected.title}.${selected.source_artifact?.format || 'bin'}`;
        anchor.click();
        URL.revokeObjectURL(url);
        this.done('Originaldatei heruntergeladen.');
      },
      error: error => this.fail(error),
    });
  }

  formulaText(formula: Record<string, unknown>): string {
    return String(formula['expression'] || formula['formula'] || JSON.stringify(formula));
  }

  private executeActions(actions: Array<Record<string, unknown>>, validators: Array<Record<string, unknown>>): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected) return;
    this.begin();
    this.api.execute(hub, {
      schema: 'ananta.spreadsheet-proposal.v1',
      proposal_id: `proposal-${crypto.randomUUID()}`,
      document_id: this.selected.document_id,
      expected_version: this.selected.version,
      base_snapshot_digest: this.selected.snapshot_digest,
      actions,
      validators,
      automatic_promotion: true,
    }).subscribe({
      next: result => {
        this.lastProposal = result;
        this.inferenceProposal = null;
        this.feedback = null;
        this.privacyPreview = null;
        this.consent = null;
        this.dataset = null;
        this.done(result.state === 'promoted'
          ? `Automatisch als Version ${result.promoted_version} promoviert.`
          : result.reason_codes.join(', ') || result.state);
        this.load(this.selected?.document_id);
      },
      error: error => this.fail(error),
    });
  }

  private loadPrivacyPreview(eventId: string): void {
    const hub = this.hubUrl();
    if (!hub) return;
    this.api.privacyPreview(hub, eventId).subscribe({
      next: preview => { this.privacyPreview = preview; this.done('Maskierte Datenschutzvorschau erzeugt.'); },
      error: error => this.fail(error),
    });
  }

  private completeWithDocument(document: SpreadsheetDocument, message: string): void {
    this.busy = false;
    this.documents = [...this.documents.filter(item => item.document_id !== document.document_id), document];
    this.select(document);
    this.message = message;
  }

  private resetLearningState(): void {
    this.lastProposal = null;
    this.feedback = null;
    this.privacyPreview = null;
    this.consent = null;
    this.dataset = null;
    this.inferenceProposal = null;
    this.training = null;
  }

  private hubUrl(): string { return this.agents.list().find(agent => agent.role === 'hub')?.url || ''; }

  private begin(): void { this.busy = true; this.clearStatus(); }
  private done(message: string): void { this.busy = false; this.message = message; this.error = ''; }
  private clearStatus(): void { this.message = ''; this.error = ''; }

  private fail(error: unknown): void {
    this.busy = false;
    const response = error as { error?: { message?: string }; message?: string };
    this.error = response.error?.message || response.message || 'Spreadsheet-Anfrage fehlgeschlagen.';
  }
}
