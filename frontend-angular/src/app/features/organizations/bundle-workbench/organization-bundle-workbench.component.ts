import { CommonModule } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { OrganizationBundlePreview } from '../models/organization-topology.models';
import { OrganizationApiClient } from '../services/organization-api.client';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';

const CLIENT_FILE_LIMIT_BYTES = 2 * 1024 * 1024;

@Component({
  selector: 'app-organization-bundle-workbench',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="workbench" aria-labelledby="bundle-workbench-heading">
      <header>
        <div><p class="eyebrow">Portable Organization Bundle v2</p><h2 id="bundle-workbench-heading">Import / Export</h2></div>
        <button type="button" (click)="exportSelected()" [disabled]="!state.selectedOrganizationId() || busy()">Bundle exportieren</button>
      </header>

      <p class="privacy">Definitionen sind direkt portabel. Optional werden nur zielseitig neu zu kompilierende Instanz-Rezepte und pseudonymisierte Assignment-Intents exportiert; Quellscope, Compiled Plans, lokale IDs, Agent-URLs und Credentials bleiben ausgeschlossen.</p>
      @if (error()) { <p class="error" role="alert">{{ error() }}</p> }
      @if (message()) { <p class="message" role="status">{{ message() }}</p> }

      <div class="input-grid">
        <label class="file">
          Bundle JSON laden (max. {{ CLIENT_FILE_LIMIT_BYTES / 1024 / 1024 }} MiB vor Serverprüfung)
          <input type="file" accept="application/json,.json" (change)="readFile($event)" />
        </label>
        <label>
          Konfliktstrategie
          <select [(ngModel)]="conflictStrategy">
            <option value="fail">Konflikte ablehnen</option>
            <option value="skip">Lokale Revision behalten</option>
            <option value="overwrite">Bestehende Definition aktualisieren</option>
          </select>
        </label>
        <button type="button" (click)="previewImport()" [disabled]="!bundle() || busy()">Schema + Semantik + Dry-run prüfen</button>
      </div>

      <fieldset class="export-options">
        <legend>Exportumfang</legend>
        <label><input type="checkbox" [(ngModel)]="exportInstances" [disabled]="exportAssignments" /> Zielseitig neu kompilierbares Instanz-Rezept</label>
        <label><input type="checkbox" [(ngModel)]="exportAssignments" (ngModelChange)="assignmentExportChanged($event)" /> Pseudonymisierte Assignment-Intents</label>
      </fieldset>

      @if (assignmentIntents().length) {
        <section class="rebindings" aria-labelledby="assignment-rebindings-heading">
          <h3 id="assignment-rebindings-heading">Target-lokale Agent-Rebindings</h3>
          <p>Die Exportdatei enthält keine Agent-URL. Ordne jeden Pseudonym-Ref vor dem Dry-run einem registrierten Ziel-Agent zu.</p>
          @for (assignment of assignmentIntents(); track assignment.principal_ref) {
            <label>
              {{ assignment.principal_label || assignment.principal_ref }} · {{ assignment.unit_key }}/{{ assignment.role_slot_key }}
              <input type="url" [(ngModel)]="assignmentRebindings[assignment.principal_ref]" autocomplete="off" placeholder="https://target-worker:port" />
            </label>
          }
        </section>
      }

      @if (customInstances().length) {
        <section class="rebindings" aria-labelledby="instance-admission-heading">
          <h3 id="instance-admission-heading">Custom-N Admission-Ausnahmen</h3>
          @for (instance of customInstances(); track instance.instance_key) {
            <label>
              {{ instance.name }}
              <input type="password" [(ngModel)]="instanceAdmissionExceptionRefs[instance.instance_key]" autocomplete="off" placeholder="zielseitiger one-shot Exception-Ref" />
            </label>
          }
        </section>
      }

      @if (preview(); as plan) {
        <section class="preview" aria-labelledby="bundle-preview-heading">
          <h3 id="bundle-preview-heading">Gebundener Importplan</h3>
          <p>Bundle {{ plan.source_version }} → {{ plan.target_version }} · Strategie {{ plan.conflict_strategy }}</p>
          @if (plan.target_rebind_contract.available) {
            <p class="privacy">Instanzmodus: {{ plan.instance_import_mode }}. Portable Rezepte werden im authentifizierten Zielprojekt mit aktuellen Definitionen und Limits neu kompiliert; pseudonymisierte Rollen werden ausschließlich über die oben bestätigten lokalen Agent-Rebindings zugeordnet.</p>
          } @else {
            <p class="privacy">Instanzmodus: {{ plan.instance_import_mode }}. Dieses Bundle enthält keinen ausgewiesenen Organization-Blueprint-Root; es importiert ausschließlich wiederverwendbare Definitionen.</p>
          }
          <div class="changes">
            @for (group of changeGroups(plan); track group[0]) {
              <article>
                <h4>{{ groupLabel(group[0]) }} <span>{{ group[1].length }}</span></h4>
                <ul>
                  @for (change of group[1]; track change.key + change.action) {
                    <li><strong>{{ change.action }}</strong> · {{ change.key }} <small>{{ change.detail || '' }}</small></li>
                  }
                </ul>
              </article>
            }
          </div>
          <h4>Portabilitätsgrenze</h4>
          <p>{{ plan.omitted_fields.length ? plan.omitted_fields.join(', ') : 'Keine Laufzeitfelder im Definitionsimport enthalten.' }}</p>
          @if (plan.diagnostics.length) {
            <ul class="diagnostics">
              @for (diagnostic of plan.diagnostics; track diagnostic.reason_code + diagnostic.message) {
                <li [attr.data-severity]="diagnostic.severity">{{ diagnostic.severity }} · {{ diagnostic.message }}<small>{{ diagnostic.reason_code }}</small></li>
              }
            </ul>
          }
          <p class="digest"><strong>Plan-Digest:</strong> <code>{{ plan.plan_digest }}</code></p>
          <label>Gebundener Import-Grant <input type="password" [(ngModel)]="adminGrant" autocomplete="off" /></label>
          <label class="confirm"><input type="checkbox" [(ngModel)]="confirmed" /> Diff, Konfliktstrategie und Redaktion bewusst bestätigen</label>
          <div class="actions">
            <button type="button" class="secondary" (click)="discardPreview()">Verwerfen</button>
            <button type="button" class="secondary" (click)="issueGrant()" [disabled]="!plan.applicable || !confirmed || busy()">One-shot Grant binden</button>
            <button type="button" (click)="applyImport()" [disabled]="!plan.applicable || !confirmed || !adminGrant.trim() || busy()">Unveränderten Plan anwenden</button>
          </div>
        </section>
      }
    </section>
  `,
  styles: [`
    .workbench { display: grid; gap: 1rem; } header { align-items: end; display: flex; gap: 1rem; justify-content: space-between; }
    h2, h3, h4, p { margin: 0; } .eyebrow { color: #76a9ff; font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    button { background: #2b6fc7; border: 0; border-radius: .4rem; color: white; cursor: pointer; padding: .55rem .75rem; } button.secondary { background: #293b56; } button:disabled { opacity: .45; }
    .privacy, .message { background: #132b31; border-left: 4px solid #54b8ad; padding: .6rem; } .error { background: #421d26; border-left: 4px solid #e86f7a; color: #ffc2c8; padding: .6rem; }
    .input-grid { align-items: end; background: #0d1728; border: 1px solid #2e4261; border-radius: .7rem; display: grid; gap: .7rem; grid-template-columns: minmax(260px, 1.4fr) minmax(180px, 1fr) auto; padding: .8rem; }
    .export-options { border: 1px solid #2e4261; border-radius: .5rem; display: flex; flex-wrap: wrap; gap: .8rem; padding: .65rem; } .export-options label { align-items: center; display: flex; }
    .rebindings { background: #132238; border: 1px solid #3a5275; border-radius: .6rem; display: grid; gap: .55rem; padding: .7rem; }
    label { display: grid; gap: .3rem; } input, select { background: #111f34; border: 1px solid #405878; border-radius: .35rem; color: white; padding: .5rem; }
    .preview { background: #0e192b; border: 1px solid #344b6d; border-radius: .7rem; display: grid; gap: .75rem; padding: .9rem; }
    .changes { display: grid; gap: .6rem; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); } article { background: #101f35; border-radius: .45rem; padding: .6rem; }
    article h4 { display: flex; justify-content: space-between; } article h4 span { background: #2a4264; border-radius: 999px; padding: .1rem .4rem; }
    ul { margin: .4rem 0 0; padding-left: 1.1rem; } small { color: #91a4c3; display: block; }
    .diagnostics { display: grid; gap: .3rem; list-style: none; padding: 0; } .diagnostics li { border-left: 3px solid #6582a9; background: #0a1423; padding: .45rem; }
    .diagnostics li[data-severity='blocker'] { border-color: #e66d78; } .diagnostics li[data-severity='warning'] { border-color: #dfa743; }
    .digest { overflow-wrap: anywhere; } .confirm { align-items: center; display: flex; } .actions { display: flex; gap: .5rem; justify-content: flex-end; }
    @media (max-width: 760px) { .input-grid { grid-template-columns: 1fr; } }
  `],
})
export class OrganizationBundleWorkbenchComponent {
  readonly CLIENT_FILE_LIMIT_BYTES = CLIENT_FILE_LIMIT_BYTES;
  readonly state = inject(OrganizationTopologyStateService);
  private readonly api = inject(OrganizationApiClient);
  readonly bundle = signal<unknown | null>(null);
  readonly preview = signal<OrganizationBundlePreview | null>(null);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly message = signal('');
  conflictStrategy = 'fail';
  adminGrant = '';
  confirmed = false;
  exportInstances = false;
  exportAssignments = false;
  assignmentRebindings: Record<string, string> = {};
  instanceAdmissionExceptionRefs: Record<string, string> = {};

  readFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    this.error.set(''); this.message.set(''); this.preview.set(null);
    if (!file) return;
    if (file.size > CLIENT_FILE_LIMIT_BYTES) {
      this.error.set('Die Datei überschreitet das vorgelagerte UI-Limit.');
      input.value = '';
      return;
    }
    file.text().then(text => {
      try {
        this.bundle.set(JSON.parse(text));
        this.assignmentRebindings = {};
        this.instanceAdmissionExceptionRefs = {};
        this.message.set(`${file.name} wurde lokal geparst; die autoritative Prüfung erfolgt im Hub-Dry-run.`);
      } catch {
        this.bundle.set(null);
        this.error.set('Die Datei ist kein gültiges JSON.');
      }
    });
  }

  previewImport(): void {
    const hubUrl = this.state.hubUrl();
    const bundle = this.bundle();
    if (!hubUrl || !bundle || this.busy()) return;
    this.busy.set(true); this.error.set(''); this.message.set('');
    this.api.previewBundle(
      hubUrl,
      bundle,
      this.conflictStrategy,
      this.assignmentRebindings,
      this.instanceAdmissionExceptionRefs,
    ).pipe(
      finalize(() => this.busy.set(false)),
    ).subscribe({
      next: preview => this.preview.set(preview),
      error: () => this.error.set('Bundle-Schema oder Semantik wurde vom Hub abgewiesen.'),
    });
  }

  applyImport(): void {
    const hubUrl = this.state.hubUrl();
    const preview = this.preview();
    if (!hubUrl || !preview?.applicable || !this.confirmed || !this.adminGrant.trim()) return;
    this.busy.set(true); this.error.set('');
    this.api.applyBundle(hubUrl, preview, this.adminGrant.trim(), idempotencyKey('organization-bundle')).pipe(
      finalize(() => this.busy.set(false)),
    ).subscribe({
      next: result => {
        this.message.set(result.replayed ? 'Der bereits angewendete Import wurde idempotent wiedergegeben.' : 'Bundle wurde atomar importiert.');
        this.discardPreview();
        this.state.initialize();
      },
      error: () => this.error.set('Der gebundene Importplan konnte nicht angewendet werden; es wurden keine Teilwrites übernommen.'),
    });
  }

  issueGrant(): void {
    const hubUrl = this.state.hubUrl();
    const preview = this.preview();
    if (!hubUrl || !preview?.applicable || !this.confirmed || this.busy()) return;
    this.busy.set(true); this.error.set('');
    this.api.issueBundleGrant(
      hubUrl,
      preview,
      idempotencyKey('organization-bundle-grant'),
    ).pipe(finalize(() => this.busy.set(false))).subscribe({
      next: grant => {
        this.adminGrant = grant.grant_id;
        this.message.set('Der one-shot Import-Grant ist exakt an Plan-, Policy- und Zieldigest gebunden.');
      },
      error: () => this.error.set('Der Import-Grant konnte wegen Scope- oder Revisionsdrift nicht gebunden werden.'),
    });
  }

  exportSelected(): void {
    const hubUrl = this.state.hubUrl();
    const organizationId = this.state.selectedOrganizationId();
    if (!hubUrl || !organizationId || this.busy()) return;
    this.busy.set(true); this.error.set('');
    this.api.exportBundle(
      hubUrl,
      organizationId,
      this.exportInstances,
      this.exportAssignments,
    ).pipe(finalize(() => this.busy.set(false))).subscribe({
      next: bundle => {
        const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        const rootDefinition = portableFileToken(bundle.bundle_metadata.root_definition_ref);
        anchor.href = url; anchor.download = `organization-${rootDefinition}.bundle.v2.json`; anchor.click();
        URL.revokeObjectURL(url);
        this.message.set('Bundle wurde mit explizit ausgewiesenem Redaktions- und Rebind-Umfang exportiert.');
      },
      error: () => this.error.set('Bundle konnte nicht exportiert werden.'),
    });
  }

  discardPreview(): void {
    this.preview.set(null); this.confirmed = false; this.adminGrant = '';
  }

  assignmentExportChanged(enabled: boolean): void {
    if (enabled) this.exportInstances = true;
  }

  assignmentIntents(): readonly {
    principal_ref: string;
    principal_label?: string;
    unit_key: string;
    role_slot_key: string;
  }[] {
    const value = this.bundle() as { assignments?: readonly {
      principal_ref?: unknown;
      principal_label?: unknown;
      unit_key?: unknown;
      role_slot_key?: unknown;
    }[] } | null;
    const unique = new Map<string, { principal_ref: string; principal_label?: string; unit_key: string; role_slot_key: string }>();
    for (const item of value?.assignments ?? []) {
      const principalRef = String(item.principal_ref || '').trim();
      if (!principalRef || unique.has(principalRef)) continue;
      unique.set(principalRef, {
        principal_ref: principalRef,
        principal_label: String(item.principal_label || '').trim() || undefined,
        unit_key: String(item.unit_key || ''),
        role_slot_key: String(item.role_slot_key || ''),
      });
    }
    return [...unique.values()];
  }

  customInstances(): readonly { instance_key: string; name: string }[] {
    const value = this.bundle() as { organization_instances?: readonly {
      instance_key?: unknown;
      name?: unknown;
      composition_mode?: unknown;
    }[] } | null;
    return (value?.organization_instances ?? [])
      .filter(item => item.composition_mode === 'custom')
      .map(item => ({
        instance_key: String(item.instance_key || ''),
        name: String(item.name || item.instance_key || ''),
      }))
      .filter(item => Boolean(item.instance_key));
  }

  changeGroups(preview: OrganizationBundlePreview): readonly [string, readonly { key: string; action: string; detail?: string }[]][] {
    return Object.entries(preview.changes);
  }

  groupLabel(key: string): string {
    return ({
      role_templates: 'Role Templates', team_blueprints: 'Team Blueprints', workflow_definitions: 'Workflows',
      organization_blueprints: 'Organization Blueprints', policies: 'Policies', organization_instances: 'Instanz-Rezepte (Ziel-Recompile)', assignments: 'Assignment-Rebindings',
    } as Record<string, string>)[key] ?? key;
  }
}

function idempotencyKey(prefix: string): string {
  const value = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}:${value}`;
}

function portableFileToken(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'bundle';
}
