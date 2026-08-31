import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, ChangeDetectorRef, Component, Input, OnChanges, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  BusinessControllingApiService,
  BusinessControllingFinding,
  BusinessControllingMapping,
  BusinessControllingProfile,
  BusinessControllingScope,
  BusinessControllingStatus,
} from './business-controlling-api.service';

@Component({
  selector: 'app-business-controlling-workbench',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section aria-labelledby="controlling-title" class="workbench">
      <header>
        <p class="eyebrow">Read-only Analyse</p>
        <h1 id="controlling-title">Business Data Quality</h1>
        @if (status) {
          <p role="status">
            Regeln {{ status.enabled ? 'aktiv' : 'deaktiviert' }} · Statistik
            {{ status.statistics_enabled ? 'opt-in aktiv' : 'aus' }} · keine Finanzaktion
          </p>
        }
      </header>

      @if (error) { <p role="alert">{{ error }}</p> }

      <form (ngSubmit)="profileImport()" aria-labelledby="import-title">
        <h2 id="import-title">Autorisierte Quelle prüfen</h2>
        <label>Source Revision <input name="sourceRevision" [(ngModel)]="sourceRevisionId" required /></label>
        <label>Revision Digest <input name="revisionDigest" [(ngModel)]="revisionDigest" required /></label>
        <label>Format
          <select name="sourceFormat" [(ngModel)]="sourceFormat">
            <option value="csv">CSV</option><option value="xlsx">XLSX</option>
          </select>
        </label>
        <button type="submit" [disabled]="busy || !status?.enabled">Profil erzeugen</button>
      </form>

      @if (profile) {
        <section aria-labelledby="mapping-title">
          <h2 id="mapping-title">Mapping prüfen</h2>
          <p>{{ profile.row_count }} Zeilen · Profil <code>{{ profile.profile_digest }}</code></p>
          <ul>
            @for (column of profile.columns; track column.header) {
              <li>{{ column.header }} → {{ column.inferred_type }}</li>
            }
          </ul>
          <button type="button" (click)="confirmIdentityMapping()" [disabled]="busy">
            Geprüftes Identitätsmapping bestätigen
          </button>
        </section>
      }

      @if (mapping) {
        <section aria-labelledby="run-title">
          <h2 id="run-title">Analyse konfigurieren</h2>
          <label><input type="checkbox" [(ngModel)]="statisticsRequested" /> Optionale Statistik</label>
          @if (statisticsRequested && status?.statistics_enabled) {
            <label>Freigegebener Katalogeintrag
              <input name="statisticalCatalogEntry" [(ngModel)]="statisticalCatalogEntryId" />
            </label>
          }
          <label><input type="checkbox" [(ngModel)]="explanationsRequested" /> Evidenz-Erklärungen</label>
          <button type="button" (click)="startRun()" [disabled]="busy">Read-only Lauf starten</button>
        </section>
      }

      <section aria-labelledby="findings-title">
        <h2 id="findings-title">Findings</h2>
        <button type="button" (click)="reloadFindings()" [disabled]="busy">Aktualisieren</button>
        <button type="button" (click)="exportFindings()" [disabled]="busy">Redigierten Export erstellen</button>
        <div class="findings">
          @for (finding of findings; track finding.finding_id) {
            <article [attr.data-kind]="finding.kind">
              <h3>{{ label(finding.kind) }} · {{ finding.severity }}</h3>
              <p>Dataset {{ finding.dataset_version }} · Regel/Modell {{ finding.rule_version }}</p>
              <p>Confidence {{ finding.confidence ?? 'deterministisch' }} · Evidence <code>{{ finding.evidence_digest }}</code></p>
              <p>Disposition: {{ finding.disposition }}</p>
              <div aria-label="Disposition setzen">
                <button type="button" (click)="setDisposition(finding, 'confirmed')">Bestätigt</button>
                <button type="button" (click)="setDisposition(finding, 'false_positive')">False Positive</button>
                <button type="button" (click)="setDisposition(finding, 'needs_data')">Daten fehlen</button>
                <button type="button" (click)="setDisposition(finding, 'accepted_exception')">Ausnahme</button>
              </div>
            </article>
          } @empty { <p>Keine Findings vorhanden.</p> }
        </div>
      </section>
      @if (exportDigest) { <p role="status">Redigierter Export: <code>{{ exportDigest }}</code></p> }
    </section>
  `,
  styles: [`
    .workbench { display: grid; gap: 1rem; }
    .eyebrow { font-size: .75rem; letter-spacing: .08em; text-transform: uppercase; }
    form, section section, article { border: 1px solid currentColor; border-radius: .5rem; padding: 1rem; }
    form, label, .findings { display: grid; gap: .5rem; }
    code { overflow-wrap: anywhere; }
    button { margin: .25rem; }
  `],
})
export class BusinessControllingWorkbenchComponent implements OnChanges {
  @Input({ required: true }) projectId = '';

  private readonly api = inject(BusinessControllingApiService);
  private readonly changes = inject(ChangeDetectorRef);
  status: BusinessControllingStatus | null = null;
  profile: BusinessControllingProfile | null = null;
  mapping: BusinessControllingMapping | null = null;
  findings: readonly BusinessControllingFinding[] = [];
  sourceRevisionId = '';
  revisionDigest = '';
  sourceFormat: 'csv' | 'xlsx' = 'csv';
  statisticsRequested = false;
  statisticalCatalogEntryId = '';
  explanationsRequested = true;
  exportDigest = '';
  error = '';
  busy = false;

  ngOnChanges(): void {
    if (!this.projectId) return;
    this.api.status(this.scope()).subscribe({
      next: status => {
        this.status = status;
        this.statisticsRequested = status.statistics_enabled;
        this.explanationsRequested = status.explanations_enabled;
        this.changes.markForCheck();
      },
      error: () => this.fail('Workbench-Status nicht verfügbar.'),
    });
    this.reloadFindings();
  }

  profileImport(): void {
    this.run(() => this.api.profileImport(this.scope(), {
      source_revision_id: this.sourceRevisionId,
      revision_digest: this.revisionDigest,
      source_format: this.sourceFormat,
    }), profile => { this.profile = profile; this.mapping = null; });
  }

  confirmIdentityMapping(): void {
    if (!this.profile) return;
    const mapping = Object.fromEntries(this.profile.columns.map(column => [column.header, column.header]));
    this.run(
      () => this.api.confirmMapping(this.scope(), this.profile!.profile_digest, mapping),
      confirmation => { this.mapping = confirmation; },
    );
  }

  startRun(): void {
    if (!this.mapping) return;
    this.run(
      () => this.api.startRun(
        this.scope(),
        this.mapping!.confirmation_digest,
        Boolean(this.status?.statistics_enabled && this.statisticsRequested),
        Boolean(this.status?.explanations_enabled && this.explanationsRequested),
        this.statisticalCatalogEntryId,
      ),
      () => this.reloadFindings(),
    );
  }

  reloadFindings(): void {
    if (!this.projectId) return;
    this.api.findings(this.scope()).subscribe({
      next: findings => { this.findings = findings; this.changes.markForCheck(); },
      error: () => this.fail('Findings konnten nicht geladen werden.'),
    });
  }

  setDisposition(finding: BusinessControllingFinding, disposition: BusinessControllingFinding['disposition']): void {
    this.run(
      () => this.api.setDisposition(this.scope(), finding, disposition),
      updated => {
        this.findings = this.findings.map(item => item.finding_id === updated.finding_id ? updated : item);
      },
    );
  }

  exportFindings(): void {
    this.run(
      () => this.api.export(this.scope()),
      report => { this.exportDigest = report.report_digest; },
    );
  }

  label(kind: BusinessControllingFinding['kind']): string {
    return ({
      deterministic_violation: 'Regelverletzung',
      reconciliation_mismatch: 'Abstimmungsdifferenz',
      statistical_anomaly: 'Statistische Auffälligkeit',
      advisory_explanation: 'Hinweis',
    })[kind];
  }

  private scope(): BusinessControllingScope {
    return { project_id: this.projectId };
  }

  private run<T>(operation: () => import('rxjs').Observable<T>, accept: (value: T) => void): void {
    this.busy = true;
    this.error = '';
    operation().subscribe({
      next: value => { accept(value); this.busy = false; this.changes.markForCheck(); },
      error: () => this.fail('Aktion wurde fail-closed abgebrochen.'),
    });
  }

  private fail(message: string): void {
    this.error = message;
    this.busy = false;
    this.changes.markForCheck();
  }
}
