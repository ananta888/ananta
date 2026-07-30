import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, ActivatedRouteSnapshot } from '@angular/router';
import { ContextPolicyRouteCapability } from '../../models/context-access-policy.model';
import { CONTEXT_POLICY_ROUTE_CAPABILITIES } from '../../services/context-access-policy-api.service';
import { ContextAccessPolicyFacade } from './context-access-policy.facade';

export function authoritativeProjectIdFromRoute(snapshot: ActivatedRouteSnapshot): string | null {
  let current: ActivatedRouteSnapshot | null = snapshot;
  while (current) {
    const dataProjectId = String(current.data?.['projectId'] || '').trim();
    if (dataProjectId) return dataProjectId;
    const projectRecord = current.data?.['project'];
    if (projectRecord && typeof projectRecord === 'object') {
      const resolvedId = String((projectRecord as Record<string, unknown>)['project_id']
        ?? (projectRecord as Record<string, unknown>)['id']
        ?? '').trim();
      if (resolvedId) return resolvedId;
    }
    const routeProjectId = String(current.paramMap.get('projectId') || '').trim();
    if (routeProjectId) return routeProjectId;
    const queryProjectId = String(
      current.queryParamMap.get('projectId') ||
        current.queryParamMap.get('project_id') ||
        '',
    ).trim();
    if (queryProjectId) return queryProjectId;
    current = current.parent;
  }
  return null;
}

@Component({
  selector: 'app-policy-overview',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <main class="policy-shell" aria-labelledby="policy-title">
      <header class="policy-hero">
        <div>
          <p class="eyebrow">Access Governance</p>
          <h1 id="policy-title">Context Policy Control</h1>
          <p>
            Globale, serverbasierte Sicht auf Policy-Versionen und ihre deklarierten
            Constraints. Diese Oberfläche trifft niemals lokal eine Allow-Entscheidung.
          </p>
        </div>
        <dl>
          <dt>Projektkontext</dt>
          <dd><code>{{ projectId || 'Nicht vorhanden' }}</code></dd>
          <dt>Management-Berechtigung</dt>
          <dd>{{ facade.managementAuthorized() ? 'Vom Listen-Endpunkt bestätigt' : 'Nicht bestätigt' }}</dd>
        </dl>
      </header>

      <div *ngIf="facade.listError() as error" class="state error" role="alert">
        <strong>{{ errorStateLabel(error.state) }}</strong>
        <span>{{ error.message }}</span>
        <code *ngIf="error.reasonCode">reason_code={{ error.reasonCode }}</code>
        <button type="button" *ngIf="projectId" (click)="reload(error.conflict)">
          {{ error.conflict ? 'Sicher neu laden' : 'Erneut laden' }}
        </button>
      </div>
      <div *ngIf="facade.listLoading()" class="state" role="status" aria-live="polite">
        Server-Policies werden geladen ...
      </div>

      <section class="capability-section" aria-labelledby="capability-heading">
        <div class="section-heading">
          <div>
            <p class="section-kicker">Serververtrag</p>
            <h2 id="capability-heading">Capability-gesteuerte Flows</h2>
          </div>
          <p>Route vorhanden bedeutet nicht automatisch Berechtigung oder Policy-Aktivierung.</p>
        </div>
        <div class="capability-grid">
          <article *ngFor="let capability of capabilities" [attr.data-flow]="capability.flow">
            <span
              class="capability-state"
              [attr.data-state]="capabilityState(capability)"
            >{{ capabilityLabel(capability) }}</span>
            <h3>{{ flowLabel(capability.flow) }}</h3>
            <p>{{ capability.reason }}</p>
          </article>
        </div>
      </section>

      <section class="version-section" aria-labelledby="versions-heading">
        <div class="section-heading">
          <div>
            <p class="section-kicker">List / Version</p>
            <h2 id="versions-heading">Servergelieferte Policy-Versionen</h2>
          </div>
          <button type="button" [disabled]="!projectId || facade.listLoading()" (click)="facade.reload()">Liste aktualisieren</button>
        </div>
        <div class="table-wrap" *ngIf="facade.policies().length; else noPolicies">
          <table>
            <caption>Maximal 200 Policy-Datensätze aus dem autoritativen Projektkontext</caption>
            <thead>
              <tr><th>Policy-ID</th><th>Version</th><th>Scope</th><th>Projekt</th><th>Aktualisiert</th><th>Detail</th></tr>
            </thead>
            <tbody>
              <tr *ngFor="let policy of facade.policies()">
                <td><code>{{ policy.policy_id }}</code></td>
                <td>{{ policy.version }}</td>
                <td>{{ policy.scope }}</td>
                <td><code>{{ policy.project_id || 'Nicht geliefert' }}</code></td>
                <td>{{ policy.updated_at || policy.created_at || 'Nicht geliefert' }}</td>
                <td>
                  <button
                    type="button"
                    [disabled]="!facade.managementAuthorized() || facade.detailLoading()"
                    [attr.aria-label]="'Latest-Version von ' + policy.policy_id + ' laden'"
                    (click)="facade.loadLatest(policy.policy_id)"
                  >Latest laden</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <ng-template #noPolicies>
          <p class="empty" *ngIf="facade.listConfirmed()">Der Server liefert für dieses Projekt keine Policy-Datensätze.</p>
        </ng-template>
        <p *ngIf="facade.recordsTruncated()" class="bounded-note">Weitere Policy-Datensätze sind in dieser Ansicht ausgeblendet.</p>
      </section>

      <section class="detail-section" aria-labelledby="detail-heading">
        <div class="section-heading">
          <div>
            <p class="section-kicker">Detail / Lifecycle</p>
            <h2 id="detail-heading">Policy-Version</h2>
          </div>
        </div>
        <div *ngIf="facade.detailError() as error" class="state error" role="alert">
          <span>{{ error.message }}</span>
          <code *ngIf="error.reasonCode">reason_code={{ error.reasonCode }}</code>
          <button type="button" *ngIf="error.conflict" (click)="facade.safeReloadAfterConflict()">Sicher neu laden</button>
        </div>
        <div *ngIf="facade.selectedPolicy() as selected; else noDetail" class="policy-detail">
          <dl>
            <div><dt>Policy-ID</dt><dd><code>{{ selected.policy_id }}</code></dd></div>
            <div><dt>Version</dt><dd>{{ selected.version }}</dd></div>
            <div><dt>Scope</dt><dd>{{ selected.scope }}</dd></div>
            <div><dt>Regeln</dt><dd>{{ selected.policy.rules.length }}</dd></div>
          </dl>
          <div class="detail-actions">
            <button
              type="button"
              data-action="server-lint"
              [disabled]="!facade.managementAuthorized() || facade.validationLoading()"
              (click)="facade.validateSelected()"
            >
              {{ facade.validationLoading() ? 'Server lintet ...' : 'Server-Lint ausführen' }}
            </button>
            <p>
              Lint prüft genau die persistierte Version. Aktivierung und Widerruf
              verwenden das vom Detail gelieferte ETag.
            </p>
            <div class="lifecycle-actions">
              <button
                type="button"
                [disabled]="facade.mutationLoading()"
                (click)="facade.activateSelected()"
              >Aktivieren</button>
              <button
                type="button"
                [disabled]="facade.mutationLoading()"
                (click)="facade.revokeSelected()"
              >Widerrufen</button>
              <label>
                Zielversion für Rollback
                <input
                  type="number"
                  min="1"
                  [ngModel]="rollbackVersion()"
                  (ngModelChange)="setRollbackVersion($event)"
                />
              </label>
              <button
                type="button"
                [disabled]="facade.mutationLoading()"
                (click)="facade.rollbackSelected(rollbackVersion())"
              >Rollback als neuen Draft erstellen</button>
            </div>
          </div>
        </div>
        <ng-template #noDetail><p class="empty">Eine servergelieferte Policy auswählen, um ihr Latest-Detail zu laden.</p></ng-template>

        <div *ngIf="facade.validationError() as error" class="state error" role="alert">
          <span>{{ error.message }}</span>
          <code *ngIf="error.reasonCode">reason_code={{ error.reasonCode }}</code>
          <button type="button" *ngIf="error.conflict" (click)="facade.safeReloadAfterConflict()">Sicher neu laden</button>
        </div>
        <div *ngIf="facade.validation() as validation" class="validation-result" role="status" aria-live="polite">
          <strong>Server-Lint: {{ validation.valid ? 'Keine Fehler' : 'Fehler vorhanden' }}</strong>
          <p>Dies ist ausdrücklich keine effektive Allow- oder Grant-Entscheidung.</p>
          <ul *ngIf="validation.errors.length">
            <li *ngFor="let error of validation.errors">{{ error }}</li>
          </ul>
        </div>
      </section>

      <section class="grant-section" aria-labelledby="grant-heading">
        <div class="section-heading">
          <div>
            <p class="section-kicker">Grant-Assistent</p>
            <h2 id="grant-heading">Serverautoritatives Grant-Preflight</h2>
          </div>
        </div>
        <ol class="grant-steps">
          <li>
            <span>1</span>
            <div><strong>Projekt</strong><p><code>{{ projectId || 'Nicht geliefert' }}</code> aus Route oder Resolver-Kontext.</p></div>
          </li>
          <li>
            <span>2</span>
            <div><strong>Preset</strong><p>Nicht verfügbar: Der Hub liefert keinen Preset-Katalog.</p></div>
          </li>
          <li>
            <span>3</span>
            <div>
              <strong>Destination</strong>
              <p>Die effektive Matrix liefert serveraufgelöste Destination-IDs.</p>
            </div>
          </li>
          <li>
            <span>4</span>
            <div>
              <strong>Operation und Transformation</strong>
              <p>
                Operation, Transformation und Zweck werden als Nutzerintention
                gesendet; der Hub entscheidet.
              </p>
            </div>
          </li>
          <li>
            <span>5</span>
            <div>
              <strong>Decision / Reason Code / Grant</strong>
              <p>Preview ist verfügbar; eine Grant-Mutationsroute fehlt weiterhin.</p>
            </div>
          </li>
        </ol>
        <button type="button" disabled aria-describedby="grant-unavailable-reason">Grant nicht verfügbar</button>
        <p id="grant-unavailable-reason" class="bounded-note">
          Presets und Grant-Mutation fehlen. Preview und Matrix bleiben reine
          serverseitige Entscheidungen und erzeugen keine Freigabe.
        </p>
      </section>

      <section class="matrix-section" aria-labelledby="matrix-heading">
        <div class="section-heading">
          <div>
            <p class="section-kicker">Globale Access-Matrix</p>
            <h2 id="matrix-heading">Effektive Serverentscheidungen</h2>
          </div>
          <p>Jede Zeile bindet eine reale SourceRevision an eine reale Destination.</p>
        </div>
        <div class="matrix-intent">
          <label>
            Operation
            <select [ngModel]="matrixOperation()" (ngModelChange)="matrixOperation.set($event)">
              <option value="chat_context">Chat-Kontext</option>
              <option value="index">Index</option>
              <option value="export">Export</option>
            </select>
          </label>
          <label>
            Transformation
            <select [ngModel]="matrixTransformation()" (ngModelChange)="matrixTransformation.set($event)">
              <option value="raw">Raw</option>
              <option value="redacted">Redacted</option>
              <option value="summary">Summary</option>
            </select>
          </label>
          <label>
            Zweck
            <input
              type="text"
              maxlength="255"
              [ngModel]="matrixPurpose()"
              (ngModelChange)="matrixPurpose.set($event)"
            />
          </label>
          <button
            type="button"
            [disabled]="!facade.managementAuthorized() || facade.matrixLoading() || !matrixPurpose().trim()"
            (click)="facade.loadEffectiveMatrix(matrixOperation(), matrixTransformation(), matrixPurpose())"
          >{{ facade.matrixLoading() ? 'Matrix wird berechnet ...' : 'Effektive Matrix laden' }}</button>
        </div>
        <div *ngIf="facade.matrixError() as error" class="state error" role="alert">
          <span>{{ error.message }}</span>
          <code *ngIf="error.reasonCode">reason_code={{ error.reasonCode }}</code>
        </div>
        <div class="table-wrap" *ngIf="facade.effectiveMatrix().length; else noMatrix">
          <table>
            <caption>Maximal 625 serverseitig ausgewertete Source-/Destination-Paare</caption>
            <thead>
              <tr>
                <th>SourceRevision</th>
                <th>Destination</th>
                <th>Operation</th>
                <th>Transformation</th>
                <th>Decision</th>
                <th>Reason Codes</th>
                <th>Policy-Digest</th>
                <th>Preview</th>
              </tr>
            </thead>
            <tbody>
              <tr *ngFor="let row of facade.effectiveMatrix()">
                <td><code>{{ row.source_revision_id }}</code></td>
                <td><code>{{ row.destination_id }}</code></td>
                <td>{{ row.operation }}</td>
                <td>{{ row.transformation }}</td>
                <td><strong>{{ row.decision }}</strong></td>
                <td>{{ row.reason_codes.join(', ') || 'Kein Reason Code' }}</td>
                <td><code>{{ row.policy_digest }}</code></td>
                <td>
                  <button
                    type="button"
                    [disabled]="!facade.selectedPolicy() || facade.validationLoading()"
                    (click)="facade.previewSelected(row.source_revision_id, row.destination_id, row.operation, row.transformation)"
                  >Mit gewählter Policy prüfen</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <ng-template #noMatrix>
          <p class="empty">Noch keine effektive Matrix geladen.</p>
        </ng-template>
        <div *ngIf="facade.preview() as preview" class="validation-result" role="status" aria-live="polite">
          <strong>Policy-Preview: {{ preview.decision }}</strong>
          <p>Reason Codes: {{ preview.reason_codes.join(', ') || 'Keine' }}</p>
          <p><code>policy_digest={{ preview.policy_digest }}</code></p>
        </div>
      </section>
    </main>
  `,
  styles: [`
    :host { display: block; color: var(--text-primary, #18221f); }
    .policy-shell { max-width: 1420px; margin: 0 auto; padding: 26px clamp(15px, 4vw, 50px) 64px; }
    .policy-hero { display: grid; grid-template-columns: 1.7fr .8fr; gap: 28px; padding: clamp(22px, 4vw, 38px); border: 1px solid #bfc9c2; border-radius: 25px; background: radial-gradient(circle at 92% 12%, #e9b957 0 8%, transparent 8.5%), linear-gradient(125deg, #dfece5, #f5efda 68%, #e7edf0); }
    .policy-hero h1 { margin: 4px 0 10px; max-width: 720px; font-family: Georgia, 'Times New Roman', serif; font-size: clamp(2.2rem, 5vw, 4.2rem); line-height: .94; letter-spacing: -.045em; }
    .policy-hero p { max-width: 780px; line-height: 1.55; }
    .policy-hero dl { margin: 0; padding: 17px; border-radius: 15px; background: rgba(255,255,255,.66); }
    .policy-hero dt { margin-top: 10px; color: #5c6965; font-size: .72rem; font-weight: 900; text-transform: uppercase; letter-spacing: .09em; }
    .policy-hero dt:first-child { margin-top: 0; }
    .policy-hero dd { margin: 4px 0; overflow-wrap: anywhere; }
    .eyebrow, .section-kicker { margin: 0; color: #496159; font-size: .7rem; font-weight: 900; letter-spacing: .15em; text-transform: uppercase; }
    section { margin-top: 22px; padding: clamp(19px, 3vw, 31px); border: 1px solid #cdd3cf; border-radius: 19px; background: #fffefa; }
    .section-heading { display: flex; justify-content: space-between; align-items: end; gap: 20px; margin-bottom: 18px; }
    .section-heading h2 { margin: 4px 0 0; font-family: Georgia, 'Times New Roman', serif; font-size: clamp(1.55rem, 3vw, 2.4rem); }
    .section-heading > p { max-width: 520px; margin: 0; color: #5d6965; text-align: right; }
    .state { display: flex; align-items: center; gap: 11px; margin-top: 17px; padding: 13px 16px; border-radius: 11px; background: #eaf2ed; }
    .state.error { flex-wrap: wrap; border-left: 5px solid #aa4035; background: #fce9e5; color: #7d2c24; }
    .state button { margin-left: auto; }
    .capability-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(205px, 1fr)); gap: 11px; }
    .capability-grid article { min-height: 142px; padding: 16px; border: 1px solid #d0d6d2; border-radius: 13px; background: #f8faf7; }
    .capability-grid h3 { margin: 11px 0 5px; font-family: Georgia, 'Times New Roman', serif; font-size: 1.2rem; }
    .capability-grid p { margin: 0; color: #5b6863; font-size: .85rem; line-height: 1.4; }
    .capability-state { display: inline-block; border-radius: 999px; padding: 4px 8px; background: #e4e7e5; color: #585f5c; font-size: .65rem; font-weight: 900; letter-spacing: .08em; text-transform: uppercase; }
    .capability-state[data-state="confirmed"] { background: #dbeadf; color: #28563d; }
    .capability-state[data-state="route"] { background: #e5edf0; color: #315766; }
    .capability-state[data-state="unavailable"] { background: #f6e5c5; color: #704e14; }
    .table-wrap { width: 100%; overflow-x: auto; border: 1px solid #d1d6d3; border-radius: 12px; }
    table { width: 100%; border-collapse: collapse; min-width: 760px; }
    .matrix-section table { min-width: 1680px; }
    caption { padding: 10px 13px; text-align: left; background: #f0f4f1; color: #57645f; }
    th, td { padding: 11px 13px; border-bottom: 1px solid #e0e4e1; text-align: left; vertical-align: top; line-height: 1.45; }
    th { color: #54615c; font-size: .68rem; text-transform: uppercase; letter-spacing: .08em; }
    tr:last-child td { border-bottom: 0; }
    td small { color: #717a77; }
    button { border: 1px solid #24594b; border-radius: 9px; padding: 9px 13px; background: #245f50; color: #fff; font: inherit; font-weight: 800; cursor: pointer; }
    button:disabled { cursor: not-allowed; opacity: .5; }
    button:focus-visible, [tabindex]:focus-visible { outline: 3px solid #dfa62f; outline-offset: 2px; }
    .policy-detail { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    .policy-detail dl { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1px; margin: 0; padding: 1px; border-radius: 12px; overflow: hidden; background: #d3d8d5; }
    .policy-detail dl div { padding: 13px; background: #fbfcf9; }
    .policy-detail dt { color: #626e69; font-size: .7rem; text-transform: uppercase; letter-spacing: .08em; }
    .policy-detail dd { margin: 5px 0 0; }
    .detail-actions { padding: 17px; border-radius: 13px; background: #edf4ef; }
    .detail-actions p { margin: 11px 0 0; color: #5d6965; line-height: 1.45; }
    .validation-result { margin-top: 14px; padding: 16px; border-left: 5px solid #4c7468; border-radius: 11px; background: #edf4ef; }
    .lifecycle-actions, .matrix-intent { display: flex; flex-wrap: wrap; align-items: end; gap: 10px; margin-top: 14px; }
    .lifecycle-actions label, .matrix-intent label { display: grid; gap: 5px; font-weight: 750; }
    .lifecycle-actions input, .matrix-intent input, .matrix-intent select { min-height: 40px; border: 1px solid #9eaaa4; border-radius: 8px; padding: 7px 9px; font: inherit; }
    .grant-steps { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; padding: 0; list-style: none; }
    .grant-steps li { min-height: 165px; padding: 14px; border: 1px solid #d2d7d4; border-radius: 13px; background: #fafbf8; }
    .grant-steps li > span { display: grid; place-items: center; width: 27px; height: 27px; border-radius: 50%; background: #263f37; color: #fff; font-weight: 900; }
    .grant-steps strong { display: block; margin-top: 12px; }
    .grant-steps p { color: #5f6b67; font-size: .86rem; line-height: 1.45; }
    .identity-gap { margin-bottom: 15px; padding: 14px; border-left: 5px solid #b97e24; border-radius: 10px; background: #fff1d4; line-height: 1.5; }
    .bounded-note, .empty { color: #68531c; background: #fff3d8; padding: 10px 13px; border-radius: 9px; }
    code { font-family: 'IBM Plex Mono', 'Courier New', monospace; font-size: .86em; }
    @media (max-width: 980px) {
      .grant-steps { grid-template-columns: repeat(2, 1fr); }
      .policy-detail { grid-template-columns: 1fr; }
    }
    @media (max-width: 720px) {
      .policy-hero { grid-template-columns: 1fr; }
      .section-heading { align-items: start; flex-direction: column; }
      .section-heading > p { text-align: left; }
      .grant-steps { grid-template-columns: 1fr; }
      .policy-detail dl { grid-template-columns: 1fr; }
    }
  `],
})
export class PolicyOverviewComponent implements OnInit {
  readonly facade = inject(ContextAccessPolicyFacade);
  private readonly route = inject(ActivatedRoute);
  readonly capabilities = CONTEXT_POLICY_ROUTE_CAPABILITIES;
  readonly projectId = authoritativeProjectIdFromRoute(this.route.snapshot);
  readonly rollbackVersion = signal(1);
  readonly matrixOperation = signal('chat_context');
  readonly matrixTransformation = signal('redacted');
  readonly matrixPurpose = signal('code_navigation');

  ngOnInit(): void {
    this.facade.initialize(this.projectId);
  }

  reload(conflict: boolean): void {
    if (conflict) this.facade.safeReloadAfterConflict();
    else this.facade.reload();
  }

  setRollbackVersion(value: number | string): void {
    const parsed = Number(value);
    this.rollbackVersion.set(
      Number.isInteger(parsed) && parsed > 0 ? parsed : 1,
    );
  }

  capabilityState(capability: ContextPolicyRouteCapability): 'confirmed' | 'route' | 'unavailable' {
    if (!capability.routeAvailable) return 'unavailable';
    if (capability.flow === 'list' && this.facade.listConfirmed()) return 'confirmed';
    if (capability.flow === 'detail' && this.facade.detailConfirmed()) return 'confirmed';
    if (capability.flow === 'validate' && this.facade.validationConfirmed()) return 'confirmed';
    if (capability.flow === 'lint' && this.facade.validationConfirmed()) return 'confirmed';
    if (capability.flow === 'version' && this.facade.listConfirmed()) return 'confirmed';
    return 'route';
  }

  capabilityLabel(capability: ContextPolicyRouteCapability): string {
    const state = this.capabilityState(capability);
    if (state === 'confirmed') return 'Server bestätigt';
    if (state === 'route') return 'Route vorhanden';
    return 'Nicht verfügbar';
  }

  flowLabel(flow: string): string {
    const labels: Record<string, string> = {
      list: 'List',
      detail: 'Detail',
      create: 'Create',
      draft: 'Draft',
      lint: 'Lint',
      validate: 'Validate',
      preview: 'Preview',
      version: 'Version',
      activate: 'Activate',
      revoke: 'Revoke',
      rollback: 'Rollback',
      presets: 'Presets',
      destinations: 'Destinations',
      grant: 'Grant',
    };
    return labels[flow] || flow;
  }

  errorStateLabel(state: string): string {
    const labels: Record<string, string> = {
      offline: 'Offline',
      unauthorized: 'Nicht angemeldet',
      forbidden: 'Nicht berechtigt',
      'not-found': 'Nicht gefunden',
      conflict: 'Versionskonflikt',
      unprocessable: 'Ungültiger Serververtrag',
      'rate-limited': 'Rate Limit',
      'server-error': 'Serverfehler',
      error: 'Fehler',
    };
    return labels[state] || 'Fehler';
  }
}
