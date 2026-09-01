import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { ExplanationNoticeComponent } from '../../shared/ui/display';
import { PageIntroComponent, SectionCardComponent } from '../../shared/ui/layout';
import { SpreadsheetStudioApiService } from './spreadsheet-studio-api.service';
import { SpreadsheetProposalReviewComponent } from './spreadsheet-proposal-review.component';
import { SpreadsheetProposalWorkflowService } from './spreadsheet-proposal-workflow.service';
import { SpreadsheetValidatorBuilderComponent } from './spreadsheet-validator-builder.component';
import { SpreadsheetWorkbookViewerComponent } from './spreadsheet-workbook-viewer.component';
import {
  SpreadsheetDataset,
  SpreadsheetDocument,
  SpreadsheetFeedbackEvent,
  SpreadsheetInferenceProposal,
  SpreadsheetPrivacyPreview,
  SpreadsheetProposalJob,
  SpreadsheetProposalResult,
  SpreadsheetRangeSelection,
  SpreadsheetStudioCapabilities,
  SpreadsheetTrainingAdmission,
  SpreadsheetTrainingConsent,
  WorkbookSnapshot,
} from './spreadsheet-studio.models';

@Component({
  selector: 'app-spreadsheet-studio-page',
  standalone: true,
  imports: [
    CommonModule,
    ExplanationNoticeComponent,
    FormsModule,
    PageIntroComponent,
    RouterLink,
    SectionCardComponent,
    SpreadsheetProposalReviewComponent,
    SpreadsheetValidatorBuilderComponent,
    SpreadsheetWorkbookViewerComponent,
  ],
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

      <app-section-card title="Vorschau und Aktion" subtitle="Versionierte Viewports, Candidate-Prüfung und Promotion bleiben getrennte Hub-Zustände.">
        @if (selected) {
          <div class="document-meta">
            <strong>{{ selected.title }} · Version {{ selected.version }}</strong>
            <code>{{ selected.snapshot_digest }}</code>
            @if (selected.source_artifact) {
              <button type="button" class="secondary" (click)="downloadOriginal()" [disabled]="busy">Original herunterladen</button>
            }
            @if (selected.source_artifact || selected.published_artifact) {
              <button type="button" (click)="downloadPublished()" [disabled]="busy">Veröffentlichte Version herunterladen</button>
            }
          </div>
          <app-spreadsheet-workbook-viewer
            [hubUrl]="hubEndpoint"
            [document]="selected"
            (documentVersionChange)="useDocumentVersion($event)"
            (selectionChange)="useRange($event)"
            (synchronizationChange)="viewerSynchronized = $event" />
          @if (rangeSelection) {
            <p class="selection-binding">
              Aktiver Bereich: {{ rangeSelection.sheet_id }}!{{ rangeSelection.start }}:{{ rangeSelection.end }} ·
              v{{ rangeSelection.document_version }} · {{ rangeSelection.snapshot_digest }}
            </p>
          }
          <div class="row">
            <label>Zelle <input [(ngModel)]="cell" maxlength="10" aria-label="Zelladresse" /></label>
            <label>Wert <input [(ngModel)]="value" maxlength="16384" aria-label="Neuer Wert" /></label>
            <label class="check"><input type="checkbox" [(ngModel)]="automaticApply" /> Nach erfolgreichem Candidate automatisch anwenden</label>
            <button type="button" (click)="execute()" [disabled]="busy || !canPrepareProposal()">Candidate erzeugen</button>
          </div>
          @if (rangeSelection) {
            <app-spreadsheet-validator-builder
              [sheetId]="rangeSelection.sheet_id"
              [start]="rangeSelection.start"
              [end]="rangeSelection.end"
              [documentVersion]="rangeSelection.document_version"
              [snapshotDigest]="rangeSelection.snapshot_digest"
              (validatorChange)="selectedValidator = $event" />
            @if (selectedValidator) { <p>Validator aktiv: {{ selectedValidator['kind'] }}</p> }
          }
        } @else { <p>Arbeitsmappe auswählen oder importieren.</p> }
        @if (proposalJob) {
          <p class="notice" aria-live="polite">
            Proposal-Job {{ proposalJob.job_id }} · {{ proposalJob.status }}
            @if (proposalJob.queue_position !== null) { · Queue {{ proposalJob.queue_position }} }
          </p>
        }
        <app-spreadsheet-proposal-review
          [proposal]="lastProposal"
          [document]="selected"
          [synchronized]="viewerSynchronized"
          [unsupportedObjects]="selected?.unsupported_objects || []"
          (apply)="applyCandidate()"
          (edit)="editCandidate()"
          (reject)="rejectCandidate()"
          (loadMore)="loadMoreDiff()" />
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
            <p>Record-Digest: <code>{{ privacyPreview.record_digest }}</code>. Ein Widerruf sperrt abgeleitete Datensätze, Jobs und Adapter automatisch.</p>
          </details>
          <label>Aufbewahrung in Tagen <input type="number" min="1" max="3650" [(ngModel)]="retentionDays" /></label>
          <button type="button" (click)="grantConsent()" [disabled]="busy || !!consent">Einwilligen</button>
        }
        @if (consent) {
          <p>Einwilligung {{ consent.state }} · Version {{ consent.version }}</p>
          @if (consent.state === 'active') {
            <button type="button" class="secondary" (click)="revokeConsent()" [disabled]="busy">Einwilligung widerrufen</button>
          }
          @if (consent.impact) {
            <p aria-live="polite">Widerrufsfolge {{ consent.impact.state }} ·
              {{ consent.impact.dataset_ids.length }} Datensätze ·
              {{ consent.impact.training_jobs.length }} Jobs ·
              {{ consent.impact.adapters.length }} Adapter · automatische Reconciliation aktiv
            </p>
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
          <div class="split-grid" aria-label="Gesperrte Dataset-Splits">
            @for (split of splitRows(); track split.name) { <span>{{ split.name }}</span><strong>{{ split.count }}</strong> }
          </div>
          @if (dataset.split_lock) {
            <p>Split-Lock {{ dataset.split_lock.state }} · <code>{{ dataset.split_lock.split_lock_digest }}</code></p>
            <label>Warnungen filtern <input [(ngModel)]="datasetWarningFilter" maxlength="80" /></label>
            @for (warning of filteredDatasetWarnings(); track warning) { <p class="warning">{{ warning }}</p> }
          }
          @if (!dataset.readiness.training_ready) { <p>{{ dataset.readiness.reason_codes.join(', ') }}</p> }
        }
        @if (training) {
          <p>ML-Intern-Job {{ training.job['id'] || training.job['job_id'] }} · {{ training.job['state'] }}</p>
        }
        <a [routerLink]="['/model-training']" [queryParams]="trainingQuery()">
          Dataset, Split-Locks, Job, Evaluation und Adapter-Lifecycle im Hub-Control-Center öffnen
        </a>
        <hr />
        <div class="fields">
          <label>Adapter-ID <input [(ngModel)]="adapterId" /></label>
          <label>Adapter-Version <input [(ngModel)]="adapterVersion" /></label>
          <label>Basismodell <input [(ngModel)]="baseModel" /></label>
        </div>
        <button type="button" (click)="infer()" [disabled]="busy || !selected || !inferenceReady()">LoRA-Vorschlag erzeugen</button>
        @if (inferenceProposal) {
          <pre>{{ inferenceProposal.result.actions | json }}</pre>
          <button type="button" (click)="applyInferenceProposal()" [disabled]="busy || !canPrepareProposal()">LoRA-Aktionen als Candidate prüfen</button>
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
    .row label.check { display:flex; align-items:center; min-width:260px; }
    .check input { width:auto; }
    input,textarea { box-sizing:border-box; width:100%; }
    textarea { resize:vertical; }
    .file-input { position:absolute; inline-size:1px; block-size:1px; opacity:0; }
    .file-button { padding:8px 12px; border:1px solid var(--border-color, #667); border-radius:4px; cursor:pointer; }
    .entry { display:flex; justify-content:space-between; width:100%; margin:6px 0; text-align:left; gap:8px; }
    .entry.selected { outline:2px solid var(--primary, #5877e8); }
    .document-meta,.result { display:flex; flex-direction:column; gap:5px; margin-bottom:12px; }
    .selection-binding { overflow-wrap:anywhere; font-size:.9rem; }
    code,pre { overflow:auto; overflow-wrap:anywhere; white-space:pre-wrap; }
    details { margin-top:12px; }
    .secondary { opacity:.85; }
    .notice { padding:10px; border-left:3px solid var(--primary, #5877e8); }
    .warning { padding:6px; border-left:3px solid var(--warning, #b7791f); }
    .split-grid { display:grid; grid-template-columns:auto auto; max-width:260px; gap:4px 12px; }
    .error { color:var(--danger, #c33); overflow-wrap:anywhere; }
    hr { margin:16px 0; opacity:.35; }
    @media(max-width:860px){.grid{grid-template-columns:1fr;}}
  `],
})
export class SpreadsheetStudioPageComponent implements OnInit, OnDestroy {
  private readonly api = inject(SpreadsheetStudioApiService);
  private readonly agents = inject(AgentDirectoryService);
  private readonly proposals = inject(SpreadsheetProposalWorkflowService);

  capabilities: SpreadsheetStudioCapabilities | null = null;
  documents: SpreadsheetDocument[] = [];
  selected: SpreadsheetDocument | null = null;
  upload: File | null = null;
  lastProposal: SpreadsheetProposalResult | null = null;
  proposalJob: SpreadsheetProposalJob | null = null;
  feedback: SpreadsheetFeedbackEvent | null = null;
  privacyPreview: SpreadsheetPrivacyPreview | null = null;
  consent: SpreadsheetTrainingConsent | null = null;
  dataset: SpreadsheetDataset | null = null;
  inferenceProposal: SpreadsheetInferenceProposal | null = null;
  training: SpreadsheetTrainingAdmission | null = null;
  rangeSelection: SpreadsheetRangeSelection | null = null;
  selectedValidator: Record<string, unknown> | null = null;
  private pendingActions: Array<Record<string, unknown>> = [];
  private pendingValidators: Array<Record<string, unknown>> = [];
  hubEndpoint = '';
  viewerSynchronized = false;
  automaticApply = true;
  datasetWarningFilter = '';
  private proposalExecution: Subscription | null = null;
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
    this.hubEndpoint = hub;
    this.api.capabilities(hub).subscribe({ next: value => this.capabilities = value, error: error => this.fail(error) });
    this.load();
  }

  ngOnDestroy(): void { this.proposalExecution?.unsubscribe(); }

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

  canPrepareProposal(): boolean {
    return Boolean(this.selected
      && this.validCell()
      && this.viewerSynchronized
      && this.isCurrentVersion()
      && !(this.selected.unsupported_objects || []).length);
  }

  useDocumentVersion(document: SpreadsheetDocument): void {
    this.selected = document;
    this.viewerSynchronized = false;
    this.lastProposal = null;
    this.rangeSelection = null;
    this.selectedValidator = null;
    this.clearStatus();
  }

  useRange(selection: SpreadsheetRangeSelection): void {
    this.rangeSelection = selection;
    this.cell = selection.start;
    this.selectedValidator = null;
  }

  execute(): void {
    if (!this.selected || !this.validCell()) return;
    const actionValue = Number.isFinite(Number(this.value)) && this.value.trim() !== '' ? Number(this.value) : this.value;
    const validators = this.selectedValidator ? [this.selectedValidator] : [{
      validator_id: `ui-validator-${crypto.randomUUID()}`,
      kind: 'equals',
      sheet_id: this.rangeSelection?.sheet_id || this.selected.snapshot.sheets[0]?.sheet_id,
      cell: this.cell.trim().toUpperCase(),
      expected: actionValue,
      minimum: null,
      maximum: null,
    }];
    this.prepareActions([{
      action_id: `ui-action-${crypto.randomUUID()}`,
      kind: 'set_value',
      sheet_id: this.rangeSelection?.sheet_id || this.selected.snapshot.sheets[0]?.sheet_id,
      cell: this.cell.trim().toUpperCase(),
      value: actionValue,
      formula: null,
    }], validators);
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
    this.prepareActions(this.inferenceProposal.result.actions, this.selectedValidator ? [this.selectedValidator] : []);
  }

  applyCandidate(): void {
    if (!this.lastProposal || !this.pendingActions.length || !this.viewerSynchronized || !this.isCurrentVersion()) return;
    if (this.lastProposal.base_version !== this.selected?.version
      || this.lastProposal.base_snapshot_digest !== this.selected.snapshot_digest) return;
    this.executeActions(this.pendingActions, this.pendingValidators, true);
  }

  editCandidate(): void {
    this.lastProposal = null;
    this.message = 'Candidate verworfen; Eingaben können geändert und erneut geprüft werden.';
  }

  rejectCandidate(): void {
    this.lastProposal = null;
    this.pendingActions = [];
    this.pendingValidators = [];
    this.message = 'Candidate lokal verworfen; keine Dokumentversion wurde verändert.';
  }

  loadMoreDiff(): void {
    const hub = this.hubUrl();
    const proposal = this.lastProposal;
    if (!hub || !proposal?.actual_diff.has_more || this.busy) return;
    this.begin();
    this.api.proposalDiff(hub, proposal.proposal_id, proposal.actual_diff.items.length).subscribe({
      next: page => {
        if (this.lastProposal?.proposal_id !== proposal.proposal_id) return;
        this.lastProposal = {
          ...proposal,
          actual_diff: {
            ...page,
            items: [...proposal.actual_diff.items, ...page.items],
          },
        };
        this.done('Weitere Diff-Seite geladen.');
      },
      error: error => this.fail(error),
    });
  }

  trainingQuery(): Record<string, string> {
    if (this.adapterId.trim()) return { tab: 'adapters', adapter_id: this.adapterId.trim() };
    const jobId = String(this.training?.job['id'] || this.training?.job['job_id'] || '');
    if (jobId) return { tab: 'jobs', job_id: jobId };
    if (this.dataset?.dataset_id) return { tab: 'datasets', dataset_id: this.dataset.dataset_id };
    return { tab: 'adapters' };
  }

  splitRows(): Array<{ name: string; count: number }> {
    return Object.entries(this.dataset?.split_counts || {}).map(([name, count]) => ({ name, count }));
  }

  filteredDatasetWarnings(): string[] {
    const filter = this.datasetWarningFilter.trim().toLowerCase();
    const warnings = [
      ...(this.dataset?.split_lock?.distribution_warnings || []),
      ...(this.dataset?.readiness.reason_codes || []),
    ];
    return [...new Set(warnings)].filter(warning => !filter || warning.toLowerCase().includes(filter));
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

  downloadPublished(): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected) return;
    this.begin();
    const selected = this.selected;
    this.api.downloadPublished(hub, selected.document_id).subscribe({
      next: response => {
        const blob = response.body;
        if (!blob) { this.fail(new Error('spreadsheet_download_empty')); return; }
        const artifact = selected.published_artifact || selected.source_artifact;
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `${selected.title}-v${selected.version}.${artifact?.format || 'xlsx'}`;
        anchor.click();
        URL.revokeObjectURL(url);
        this.done('Veröffentlichte Arbeitsmappe heruntergeladen.');
      },
      error: error => this.fail(error),
    });
  }

  private prepareActions(actions: Array<Record<string, unknown>>, validators: Array<Record<string, unknown>>): void {
    this.pendingActions = actions.map(action => ({ ...action }));
    this.pendingValidators = validators.map(validator => ({ ...validator }));
    this.executeActions(this.pendingActions, this.pendingValidators, false);
  }

  private executeActions(
    actions: Array<Record<string, unknown>>,
    validators: Array<Record<string, unknown>>,
    automaticPromotion: boolean,
  ): void {
    const hub = this.hubUrl();
    if (!hub || !this.selected) return;
    this.proposalExecution?.unsubscribe();
    this.proposalJob = null;
    this.begin();
    this.proposalExecution = this.proposals.execute(hub, {
      schema: 'ananta.spreadsheet-proposal.v1',
      proposal_id: `proposal-${crypto.randomUUID()}`,
      document_id: this.selected.document_id,
      expected_version: this.selected.version,
      base_snapshot_digest: this.selected.snapshot_digest,
      actions,
      validators,
      automatic_promotion: automaticPromotion,
    }).subscribe({
      next: event => {
        if (event.kind === 'job') {
          this.proposalJob = event.job;
          this.message = `Proposal-Job ${event.job.status}; automatische Hub-Ausführung läuft.`;
        } else {
          this.acceptProposalResult(event.result, automaticPromotion);
        }
      },
      error: error => this.fail(error),
    });
  }

  private acceptProposalResult(result: SpreadsheetProposalResult, automaticPromotion: boolean): void {
    this.lastProposal = result;
    this.proposalJob = null;
    this.inferenceProposal = null;
    this.feedback = null;
    this.privacyPreview = null;
    this.consent = null;
    this.dataset = null;
    if (!automaticPromotion && result.state === 'candidate_ready' && this.automaticApply) {
      this.message = 'Candidate validiert; digestgebundene automatische Promotion läuft.';
      this.applyCandidate();
      return;
    }
    this.done(result.state === 'promoted'
      ? `Automatisch als Version ${result.promoted_version} promoviert.`
      : result.reason_codes.join(', ') || result.state);
    if (result.state === 'promoted') {
      this.pendingActions = [];
      this.pendingValidators = [];
      this.load(this.selected?.document_id);
    }
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
    this.proposalJob = null;
    this.proposalExecution?.unsubscribe();
    this.feedback = null;
    this.privacyPreview = null;
    this.consent = null;
    this.dataset = null;
    this.inferenceProposal = null;
    this.training = null;
    this.rangeSelection = null;
    this.selectedValidator = null;
    this.pendingActions = [];
    this.pendingValidators = [];
    this.viewerSynchronized = false;
  }

  private isCurrentVersion(): boolean {
    if (!this.selected) return false;
    const current = this.documents.find(item => item.document_id === this.selected?.document_id);
    return Boolean(current
      && current.version === this.selected.version
      && current.snapshot_digest === this.selected.snapshot_digest);
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
