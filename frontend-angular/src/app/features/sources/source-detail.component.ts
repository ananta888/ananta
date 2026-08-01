import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { SourceDetailFacade } from './source-detail.facade';

type DetailTab = 'overview' | 'revisions' | 'runs' | 'codecompass' | 'access' | 'audit' | 'settings';

interface DetailTabDefinition {
  id: DetailTab;
  label: string;
}

@Component({
  selector: 'app-source-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <main class="detail-shell" aria-labelledby="source-detail-title">
      <a routerLink="/sources" queryParamsHandling="preserve" class="back-link">← Quellenübersicht</a>

      <div *ngIf="facade.sourceError() as error" class="state-card error" role="alert">
        <span>{{ stateLabel(error.state) }}</span>
        <h1 id="source-detail-title">Quelle nicht verfügbar</h1>
        <p>{{ error.message }}</p>
        <button type="button" (click)="retry()">Erneut laden</button>
      </div>

      <div *ngIf="!facade.sourceError()">
        <header class="detail-hero">
          <div>
            <p class="eyebrow">Source Control Center</p>
            <h1 id="source-detail-title">{{ facade.source()?.displayName || 'Quelle wird geladen' }}</h1>
            <p class="source-id"><code>{{ sourceId }}</code></p>
          </div>
          <span class="source-status">{{ facade.source()?.status || 'lädt' }}</span>
        </header>

        <div *ngIf="facade.loading()" class="loading-line" role="status" aria-live="polite">
          Reale Hub-Daten werden geladen ...
        </div>

        <section class="health-grid" aria-label="Quellenzustand">
          <article>
            <span>Active</span>
            <strong>{{ triState(facade.active()) }}</strong>
            <small>Vom serverseitigen Active-Index-Zeiger abgeleitet.</small>
          </article>
          <article>
            <span>Stale</span>
            <strong>{{ triState(facade.stale()) }}</strong>
            <small>Vom kanonischen Hub-Read-Model geliefert.</small>
          </article>
          <article>
            <span>Coverage</span>
            <strong>{{ coverageLabel() }}</strong>
            <small>Nur servergelieferte <code>coverage_percent</code>.</small>
          </article>
          <article class="next-action">
            <span>Next Action</span>
            <strong>{{ facade.nextAction() }}</strong>
            <small>Nur serverseitig bestätigte Aktionen werden angeboten.</small>
          </article>
        </section>

        <div class="tabs" role="tablist" aria-label="Quellendetails">
          <button
            *ngFor="let tab of tabs; let index = index"
            type="button"
            role="tab"
            [id]="'source-tab-' + tab.id"
            [attr.aria-controls]="'source-panel-' + tab.id"
            [attr.aria-selected]="activeTab() === tab.id"
            [attr.tabindex]="activeTab() === tab.id ? 0 : -1"
            (click)="selectTab(tab.id)"
            (keydown.arrowright)="moveTab(index, 1, $event)"
            (keydown.arrowleft)="moveTab(index, -1, $event)"
          >
            {{ tab.label }}
          </button>
        </div>

        <section
          *ngIf="activeTab() === 'overview'"
          id="source-panel-overview"
          role="tabpanel"
          aria-labelledby="source-tab-overview"
          tabindex="0"
          class="panel"
        >
          <h2>Übersicht</h2>
          <dl class="facts" *ngIf="facade.source() as source; else sourcePending">
            <div><dt>Source-ID</dt><dd><code>{{ source.sourceId }}</code></dd></div>
            <div><dt>Typ</dt><dd>{{ source.sourceType }}</dd></div>
            <div><dt>Status</dt><dd>{{ source.status }}</dd></div>
            <div><dt>Erstellt</dt><dd>{{ source.createdAt || 'Nicht geliefert' }}</dd></div>
            <div><dt>Aktualisiert</dt><dd>{{ source.updatedAt || 'Nicht geliefert' }}</dd></div>
          </dl>
          <ng-template #sourcePending><p>Quellendeskriptor wird geladen.</p></ng-template>
        </section>

        <section
          *ngIf="activeTab() === 'revisions'"
          id="source-panel-revisions"
          role="tabpanel"
          aria-labelledby="source-tab-revisions"
          tabindex="0"
          class="panel"
        >
          <h2>Revisionen</h2>
          <div *ngIf="facade.revisionsError() as error" class="notice error" role="alert">{{ error.message }}</div>
          <div class="table-wrap" *ngIf="facade.revisions().length; else noRevisions">
            <table>
              <caption>Maximal 100 servergelieferte Snapshots</caption>
              <thead><tr><th>Snapshot-ID</th><th>Status</th><th>Content-Hash</th><th>Erstellt</th></tr></thead>
              <tbody>
                <tr *ngFor="let revision of facade.revisions()">
                  <td><code>{{ revision.snapshotId }}</code></td>
                  <td>{{ revision.status }}</td>
                  <td><code>{{ revision.contentHash || 'Nicht geliefert' }}</code></td>
                  <td>{{ revision.createdAt || 'Nicht geliefert' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <ng-template #noRevisions><p>Der Hub liefert für diese Quelle keine Snapshots.</p></ng-template>
          <p *ngIf="facade.revisionsTruncated()" class="bounded-note">Weitere Snapshot-Zeilen wurden zugunsten einer begrenzten Ansicht ausgeblendet.</p>
        </section>

        <section
          *ngIf="activeTab() === 'runs'"
          id="source-panel-runs"
          role="tabpanel"
          aria-labelledby="source-tab-runs"
          tabindex="0"
          class="panel"
        >
          <h2>Runs</h2>
          <div *ngIf="facade.indexError() as error" class="notice error" role="alert">{{ error.message }}</div>
          <div *ngIf="facade.mutationError() as error" class="notice error" role="alert">{{ error.message }}</div>
          <div *ngIf="facade.lifecycleMessage()" class="notice success" role="status">
            {{ facade.lifecycleMessage() }}
          </div>
          <div class="run-editor">
            <label for="index-profile">Serverseitiges Indexprofil</label>
            <select
              id="index-profile"
              data-testid="index-profile"
              [ngModel]="selectedIndexProfileId()"
              (ngModelChange)="selectedIndexProfileId.set($event)"
            >
              <option value="">Profil auswählen</option>
              <option *ngFor="let profile of facade.indexProfiles()" [value]="profile.profileId">
                {{ profile.label }}{{ profile.isDefault ? ' (Default)' : '' }}
              </option>
            </select>
            <button
              type="button"
              data-testid="index-start"
              [disabled]="facade.mutationLoading() || !facade.can('index') || !selectedIndexProfileId()"
              (click)="facade.startIndex(selectedIndexProfileId())"
            >
              Indexlauf starten
            </button>
          </div>
          <div class="table-wrap" *ngIf="facade.runs().length; else noRun">
            <table>
              <caption>Cursorbasierte, auf 100 Einträge begrenzte Indexhistorie</caption>
              <thead><tr><th>Index-ID</th><th>Status</th><th>Erstellt</th><th>Aktualisiert</th><th>Lifecycle</th></tr></thead>
              <tbody>
                <tr *ngFor="let run of facade.runs()">
                  <td><code>{{ run.indexId }}</code></td>
                  <td>{{ run.status }}</td>
                  <td>{{ run.createdAt || 'Nicht geliefert' }}</td>
                  <td>{{ run.updatedAt || 'Nicht geliefert' }}</td>
                  <td class="row-actions">
                    <button
                      type="button"
                      data-testid="index-activate"
                      [disabled]="facade.mutationLoading() || !facade.can('activate')"
                      (click)="facade.activateIndex(run.indexId)"
                    >
                      Aktivieren
                    </button>
                    <button
                      type="button"
                      data-testid="index-rollback"
                      [disabled]="facade.mutationLoading() || !facade.can('rollback')"
                      (click)="facade.rollbackIndex(run.indexId)"
                    >
                      Rollback
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <ng-template #noRun><p>Kein zugeordneter Knowledge-Index wurde geliefert.</p></ng-template>
          <p *ngIf="facade.runsTruncated()" class="bounded-note">Weitere Runs sind über den serverseitigen Cursor verfügbar.</p>
        </section>

        <section
          *ngIf="activeTab() === 'codecompass'"
          id="source-panel-codecompass"
          role="tabpanel"
          aria-labelledby="source-tab-codecompass"
          tabindex="0"
          class="panel"
        >
          <h2>CodeCompass</h2>
          <div *ngIf="facade.indexError() as error" class="notice error" role="alert">{{ error.message }}</div>
          <div *ngIf="facade.index() as index; else noIndex">
            <div class="index-strip">
              <span>Index <code>{{ index.indexId }}</code></span>
              <span>Status {{ index.status }}</span>
              <button type="button" [disabled]="facade.graphLoading()" (click)="facade.loadGraph()">
                {{ facade.graphLoading() ? 'Graph wird geladen ...' : 'Graph laden' }}
              </button>
            </div>
            <div *ngIf="facade.graphError() as error" class="notice error" role="alert">{{ error.message }}</div>
            <span class="sr-only" aria-live="polite">
              {{ facade.graphLoading() ? 'CodeCompass-Graph wird geladen.' : (facade.graphNodes().length ? 'CodeCompass-Graph wurde geladen.' : '') }}
            </span>

            <div *ngIf="facade.graphNodes().length" class="graph-region">
              <div class="graph-visual" aria-hidden="true">
                <span *ngFor="let node of visualNodes(); let index = index" [style.--offset]="index">
                  {{ node.label }}
                </span>
                <strong>{{ facade.graphEdges().length }} Kanten</strong>
              </div>
              <section id="graph-text-alternative" class="graph-text" aria-labelledby="graph-text-heading">
                <h3 id="graph-text-heading">Textuelle Graph-Alternative</h3>
                <p>{{ facade.graphTextAlternative() }}</p>
                <p>
                  Artefaktstatus:
                  <code>{{ facade.artifactStatus() | json }}</code>
                </p>
                <div class="table-wrap">
                  <table>
                    <caption>CodeCompass-Knoten, maximal 100</caption>
                    <thead><tr><th>ID</th><th>Label</th><th>Typ</th></tr></thead>
                    <tbody>
                      <tr *ngFor="let node of facade.graphNodes()">
                        <td><code>{{ node.id }}</code></td><td>{{ node.label }}</td><td>{{ node.kind }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <details>
                  <summary>Kanten als Text anzeigen</summary>
                  <ul>
                    <li *ngFor="let edge of facade.graphEdges()">
                      <code>{{ edge.source }}</code> → <code>{{ edge.target }}</code> ({{ edge.kind }})
                    </li>
                  </ul>
                </details>
              </section>
              <p *ngIf="facade.graphTruncated()" class="bounded-note">Der Graph wurde auf 100 Knoten und 200 Kanten begrenzt.</p>
            </div>
          </div>
          <ng-template #noIndex>
            <div class="capability-note">Kein realer Index ist dieser Source-ID zugeordnet. Index-Admission und Lifecycle sind nicht verfügbar.</div>
          </ng-template>
        </section>

        <section
          *ngIf="activeTab() === 'access'"
          id="source-panel-access"
          role="tabpanel"
          aria-labelledby="source-tab-access"
          tabindex="0"
          class="panel"
        >
          <h2>Zugriff</h2>
          <div *ngIf="facade.governanceError() as error" class="notice error" role="alert">
            {{ error.message }}
          </div>
          <div *ngIf="facade.lifecycleMessage()" class="notice success" role="status">
            {{ facade.lifecycleMessage() }}
          </div>

          <section class="grant-editor" aria-labelledby="grant-editor-title">
            <h3 id="grant-editor-title">Revisionsgebundenen Grant ausstellen</h3>
            <p>
              Ziel und aktive Policy werden bewusst eingegeben. Operation, Transformation,
              Purpose und maximale Laufzeit stammen ausschließlich aus dem Hub-Preset.
            </p>
            <label for="grant-destination">Hub-Destination-ID</label>
            <input
              id="grant-destination"
              data-testid="grant-destination"
              [ngModel]="grantDestinationId()"
              (ngModelChange)="grantDestinationId.set($event)"
            />
            <label for="grant-policy">Policy-ID</label>
            <input
              id="grant-policy"
              data-testid="grant-policy"
              [ngModel]="grantPolicyId()"
              (ngModelChange)="grantPolicyId.set($event)"
            />
            <label for="grant-policy-etag">ETag der aktiven Policy</label>
            <input
              id="grant-policy-etag"
              data-testid="grant-policy-etag"
              [ngModel]="grantPolicyEtag()"
              (ngModelChange)="grantPolicyEtag.set($event)"
            />
            <label for="grant-preset">Serverseitiges Grant-Preset</label>
            <select
              id="grant-preset"
              data-testid="grant-preset"
              [ngModel]="grantPresetId()"
              (ngModelChange)="grantPresetId.set($event)"
            >
              <option value="">Preset auswählen</option>
              <option *ngFor="let preset of facade.grantPresets()" [value]="preset.presetId">
                {{ preset.label }} · {{ preset.operation }} · max.
                {{ preset.maxDurationSeconds }} s
              </option>
            </select>
            <label for="grant-duration">Laufzeit in Sekunden</label>
            <input
              id="grant-duration"
              data-testid="grant-duration"
              type="number"
              min="1"
              [ngModel]="grantDurationSeconds()"
              (ngModelChange)="grantDurationSeconds.set($event)"
            />
            <button
              type="button"
              data-testid="grant-create"
              [disabled]="facade.governanceLoading()"
              (click)="createGrant()"
            >
              {{ facade.governanceLoading() ? 'Hub prüft ...' : 'Grant ausstellen' }}
            </button>
          </section>

          <div class="table-wrap" *ngIf="facade.grants().length; else noGrants">
            <table>
              <caption>Servergelieferte, revisionsgebundene Grants</caption>
              <thead><tr><th>Grant-ID</th><th>Destination</th><th>Preset</th><th>Operation</th><th>Transformation</th><th>Status</th><th>Gültig bis</th><th>Aktion</th></tr></thead>
              <tbody>
                <tr *ngFor="let grant of facade.grants()">
                  <td><code>{{ grant.grantId }}</code></td>
                  <td><code>{{ grant.destinationId }}</code></td>
                  <td><code>{{ grant.presetId || 'Kein Preset' }}</code></td>
                  <td>{{ grant.operation }}</td>
                  <td>{{ grant.transformation }}</td>
                  <td>{{ grant.state }}</td>
                  <td>{{ grant.expiresAt }}</td>
                  <td>
                    <button
                      type="button"
                      data-testid="grant-revoke"
                      [disabled]="facade.governanceLoading() || grant.state !== 'active'"
                      (click)="facade.revokeGrant(grant.grantId)"
                    >
                      Widerrufen
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <ng-template #noGrants>
            <div class="capability-note">Der Hub liefert für diese Revision keine Grants.</div>
          </ng-template>
        </section>

        <section
          *ngIf="activeTab() === 'audit'"
          id="source-panel-audit"
          role="tabpanel"
          aria-labelledby="source-tab-audit"
          tabindex="0"
          class="panel"
        >
          <h2>Audit</h2>
          <button type="button" [disabled]="facade.auditLoading()" (click)="facade.loadAudit()">
            {{ facade.auditLoading() ? 'Events werden geladen ...' : 'Hub-Events laden' }}
          </button>
          <div *ngIf="facade.auditError() as error" class="notice error" role="alert">{{ error.message }}</div>
          <div class="table-wrap" *ngIf="facade.auditEvents().length; else noAudit">
            <table>
              <caption>Inhaltsfreie, sequenzierte Source-/Index-Events</caption>
              <thead><tr><th>Sequenz</th><th>Event</th><th>Status</th><th>Reason Code</th><th>Zeit</th></tr></thead>
              <tbody>
                <tr *ngFor="let event of facade.auditEvents()">
                  <td>{{ event.sequence }}</td>
                  <td>{{ event.event_type }}</td>
                  <td>{{ event.status }}</td>
                  <td><code>{{ event.reason_code || 'Keiner' }}</code></td>
                  <td>{{ event.occurred_at }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <ng-template #noAudit><p class="capability-note">Noch keine Events geladen oder keine Events für diese Connection vorhanden.</p></ng-template>
        </section>

        <section
          *ngIf="activeTab() === 'settings'"
          id="source-panel-settings"
          role="tabpanel"
          aria-labelledby="source-tab-settings"
          tabindex="0"
          class="panel"
        >
          <h2>Einstellungen</h2>
          <div *ngIf="facade.mutationError() as error" class="notice error" role="alert">{{ error.message }}</div>
          <div class="settings-actions">
            <button
              type="button"
              [disabled]="facade.mutationLoading() || !facade.can('refresh')"
              (click)="facade.refresh()"
            >Aktualisieren</button>
            <button
              type="button"
              [disabled]="facade.mutationLoading() || !facade.can('scan')"
              (click)="facade.scan()"
            >Sicher scannen</button>
            <button
              type="button"
              class="danger"
              [disabled]="facade.mutationLoading() || !facade.can('disable')"
              (click)="facade.disable()"
            >Quelle deaktivieren</button>
          </div>
          <details *ngIf="facade.source()">
            <summary>Servergelieferte Metadaten</summary>
            <pre>{{ metadataJson() }}</pre>
          </details>
        </section>
      </div>
    </main>
  `,
  styles: [`
    :host { display: block; color: var(--text-primary, #17221f); }
    .detail-shell { max-width: 1240px; margin: 0 auto; padding: 24px clamp(15px, 4vw, 48px) 60px; }
    .back-link { display: inline-block; margin-bottom: 16px; color: #28594c; font-weight: 800; }
    .detail-hero { display: flex; justify-content: space-between; align-items: end; gap: 24px; padding: 28px; border-radius: 22px; background: radial-gradient(circle at 86% 16%, #f8dca3 0 10%, transparent 11%), linear-gradient(120deg, #e4efe7, #f6f2df); border: 1px solid #bdcbc1; }
    .detail-hero h1, .state-card h1 { margin: 4px 0; font-family: Georgia, 'Times New Roman', serif; font-size: clamp(2rem, 5vw, 3.4rem); letter-spacing: -.035em; line-height: 1; }
    .eyebrow { text-transform: uppercase; letter-spacing: .15em; color: #4a655d; font-size: .72rem; font-weight: 900; }
    .source-id { margin: 8px 0 0; }
    .source-status { border: 1px solid #507468; border-radius: 999px; padding: 7px 12px; background: #f9fcf8; font-weight: 850; text-transform: uppercase; font-size: .72rem; letter-spacing: .08em; }
    .loading-line { margin: 12px 0; border-left: 4px solid #44806c; padding: 9px 12px; background: #edf5ef; }
    .health-grid { display: grid; grid-template-columns: repeat(3, 1fr) 1.5fr; gap: 11px; margin: 18px 0; }
    .health-grid article { min-height: 105px; padding: 17px; border: 1px solid #ccd4ce; border-radius: 15px; background: #fffefa; display: flex; flex-direction: column; gap: 6px; }
    .health-grid span { color: #5d6a65; text-transform: uppercase; letter-spacing: .1em; font-size: .68rem; font-weight: 850; }
    .health-grid strong { font-family: Georgia, 'Times New Roman', serif; font-size: 1.25rem; }
    .health-grid small { margin-top: auto; color: #66716d; line-height: 1.35; }
    .health-grid .next-action { background: #f6ecd6; border-color: #d5bd8b; }
    .tabs { display: flex; gap: 4px; overflow-x: auto; border-bottom: 1px solid #aebdb5; padding: 0 4px; }
    .tabs button { border: 0; border-radius: 9px 9px 0 0; background: transparent; padding: 12px 14px; color: #53635d; font: inherit; font-weight: 800; white-space: nowrap; }
    .tabs button[aria-selected="true"] { background: #1e5c4d; color: white; }
    button:focus-visible, a:focus-visible, [tabindex="0"]:focus-visible { outline: 3px solid #e1a72b; outline-offset: 2px; }
    .panel { min-height: 290px; padding: clamp(20px, 4vw, 36px); border: 1px solid #ccd3cf; border-top: 0; border-radius: 0 0 18px 18px; background: #fff; }
    .panel h2 { margin: 0 0 20px; font-family: Georgia, 'Times New Roman', serif; font-size: 2rem; }
    .facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; padding: 1px; margin: 0; background: #d4d9d6; border-radius: 13px; overflow: hidden; }
    .facts div { padding: 15px; background: #fffefa; }
    .facts dt { color: #65716c; font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; }
    .facts dd { margin: 5px 0 0; overflow-wrap: anywhere; }
    .table-wrap { max-width: 100%; overflow-x: auto; border: 1px solid #d0d6d2; border-radius: 12px; }
    table { width: 100%; border-collapse: collapse; }
    caption { padding: 10px 13px; text-align: left; color: #5c6a65; background: #f2f5f2; }
    th, td { padding: 11px 13px; border-bottom: 1px solid #e0e4e1; text-align: left; vertical-align: top; }
    th { color: #52615c; font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; }
    tr:last-child td { border-bottom: 0; }
    code, pre { font-family: 'IBM Plex Mono', 'Courier New', monospace; font-size: .86em; }
    pre { max-height: 360px; overflow: auto; border-radius: 10px; background: #172621; color: #e5eee9; padding: 14px; }
    .capability-note, .notice, .state-card { border-left: 5px solid #bd842b; border-radius: 12px; background: #fff2d7; padding: 17px; line-height: 1.5; margin-bottom: 18px; }
    .notice.error, .state-card.error { border-left-color: #aa3d32; background: #fceae6; color: #7b2921; }
    .state-card { max-width: 720px; margin: 40px auto; }
    .state-card button, .index-strip button, .panel > button, .settings-actions button { border: 1px solid #245748; border-radius: 9px; background: #245f4f; color: #fff; padding: 9px 13px; font: inherit; font-weight: 800; }
    .index-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; margin-bottom: 17px; padding: 12px; background: #eef4ef; border-radius: 11px; }
    .index-strip button { margin-left: auto; }
    .graph-region { display: grid; gap: 18px; }
    .graph-visual { min-height: 210px; overflow: hidden; padding: 22px; border-radius: 16px; color: #eaf4ef; background: linear-gradient(145deg, #153d34, #245e50 62%, #7b683c); display: flex; flex-wrap: wrap; align-content: center; gap: 10px; }
    .graph-visual span { max-width: 190px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; border: 1px solid rgba(255,255,255,.38); border-radius: 999px; padding: 7px 11px; background: rgba(255,255,255,.11); transform: translateY(calc((var(--offset) % 3) * 5px)); }
    .graph-visual strong { flex-basis: 100%; margin-top: 10px; }
    .graph-text h3 { font-family: Georgia, 'Times New Roman', serif; font-size: 1.45rem; }
    details { margin-top: 16px; }
    summary { cursor: pointer; font-weight: 850; }
    details ul { max-height: 320px; overflow: auto; }
    .bounded-note { color: #6b561f; background: #fff4d9; padding: 9px 12px; border-radius: 8px; }
    .run-editor, .grant-editor { display: grid; grid-template-columns: minmax(12rem, 1fr) auto; align-items: end; gap: 9px 14px; margin-bottom: 18px; padding: 16px; border: 1px solid #c7d2cc; border-radius: 12px; background: #f6f8f6; }
    .grant-editor h3, .grant-editor p { grid-column: 1 / -1; }
    .grant-editor label, .grant-editor input, .grant-editor select { grid-column: 1 / -1; }
    .run-editor select, .grant-editor input, .grant-editor select { min-height: 42px; border: 1px solid #71827a; border-radius: 7px; padding: 7px 9px; background: #fff; color: inherit; font: inherit; }
    .run-editor button, .grant-editor button, .row-actions button { min-height: 42px; border: 1px solid #245748; border-radius: 8px; padding: 8px 11px; background: #245f4f; color: #fff; font: inherit; font-weight: 800; }
    .row-actions { display: flex; flex-wrap: wrap; gap: 7px; }
    .notice.success { border-left-color: #2d7559; background: #e7f5ed; color: #174c38; }
    .settings-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; }
    .settings-actions .danger { border-color: #8b3028; background: #a33a30; }
    button:disabled { cursor: not-allowed; opacity: .52; }
    .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
    @media (max-width: 850px) {
      .health-grid { grid-template-columns: repeat(2, 1fr); }
      .facts { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      .detail-hero { align-items: start; flex-direction: column; }
      .health-grid { grid-template-columns: 1fr; }
      .run-editor { grid-template-columns: 1fr; }
    }
  `],
})
export class SourceDetailComponent {
  readonly facade = inject(SourceDetailFacade);
  private readonly route = inject(ActivatedRoute);

  readonly tabs: readonly DetailTabDefinition[] = [
    { id: 'overview', label: 'Übersicht' },
    { id: 'revisions', label: 'Revisionen' },
    { id: 'runs', label: 'Runs' },
    { id: 'codecompass', label: 'CodeCompass' },
    { id: 'access', label: 'Zugriff' },
    { id: 'audit', label: 'Audit' },
    { id: 'settings', label: 'Einstellungen' },
  ];
  readonly activeTab = signal<DetailTab>('overview');
  readonly selectedIndexProfileId = signal('');
  readonly grantDestinationId = signal('');
  readonly grantPolicyId = signal('');
  readonly grantPolicyEtag = signal('');
  readonly grantPresetId = signal('');
  readonly grantDurationSeconds = signal(900);
  readonly sourceId = String(this.route.snapshot.paramMap.get('sourceId') || '').trim();

  constructor() {
    this.facade.load(this.sourceId);
  }

  retry(): void {
    this.facade.load(this.sourceId);
  }

  createGrant(): void {
    this.facade.createGrant({
      destinationId: this.grantDestinationId(),
      policyId: this.grantPolicyId(),
      policyEtag: this.grantPolicyEtag(),
      presetId: this.grantPresetId(),
      durationSeconds: Number(this.grantDurationSeconds()),
    });
  }

  selectTab(tab: DetailTab): void {
    this.activeTab.set(tab);
    if (tab === 'codecompass' && this.facade.index() && this.facade.graphNodes().length === 0) {
      this.facade.loadGraph();
    }
  }

  moveTab(currentIndex: number, delta: number, event: Event): void {
    event.preventDefault();
    const nextIndex = (currentIndex + delta + this.tabs.length) % this.tabs.length;
    this.selectTab(this.tabs[nextIndex].id);
    const buttons = (event.currentTarget as HTMLElement).parentElement?.querySelectorAll<HTMLElement>('[role="tab"]');
    buttons?.item(nextIndex).focus();
  }

  visualNodes() {
    return this.facade.graphNodes().slice(0, 24);
  }

  triState(value: boolean | null): string {
    if (value === true) return 'Ja';
    if (value === false) return 'Nein';
    return 'Nicht verfügbar';
  }

  coverageLabel(): string {
    const value = this.facade.coveragePercent();
    return value === null ? 'Nicht verfügbar' : `${value.toFixed(1)} %`;
  }

  metadataJson(): string {
    return JSON.stringify(this.facade.source()?.metadata ?? {}, null, 2);
  }

  stateLabel(state: string): string {
    const labels: Record<string, string> = {
      offline: 'Offline',
      unauthorized: 'Nicht angemeldet',
      forbidden: 'Zugriff verweigert',
      'not-found': 'Nicht gefunden',
      conflict: 'Versionskonflikt',
      unprocessable: 'Nicht verarbeitbar',
      'rate-limited': 'Rate Limit',
      'server-error': 'Serverfehler',
      error: 'Fehler',
    };
    return labels[state] || 'Fehler';
  }
}
