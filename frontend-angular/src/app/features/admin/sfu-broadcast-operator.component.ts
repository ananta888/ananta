import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  SfuBroadcastOperationsApiService,
  type SfuBroadcastCommandName,
  type SfuBroadcastCommandResult,
  type SfuBroadcastOperationItem,
  type SfuBroadcastOperationsPage,
  type SfuBroadcastQualityPreference,
  type SfuDiagnosticAtom,
} from '../../services/sfu-broadcast-operations-api.service';

@Component({
  standalone: true,
  selector: 'app-sfu-broadcast-operator',
  imports: [FormsModule],
  template: `
    <main class="sfb-ops" aria-labelledby="sfb-ops-title">
      <header class="hero">
        <div>
          <p class="eyebrow">Hub control plane / content-free</p>
          <h1 id="sfb-ops-title">SFU Broadcast Operations</h1>
          <p class="lede">Aggregierte Diagnose lesen. Aenderungen werden erst nach bestaetigter Hubantwort wirksam.</p>
        </div>
        <div class="evidence" aria-label="Evidence-Status">
          <span class="signal" [attr.data-state]="page ? 'known' : 'unknown'"></span>
          <div>
            <strong>{{ page ? 'Hub-Snapshot vorhanden' : 'Kein Hub-Snapshot' }}</strong>
            <small>Source-/Run-Evidence ist nicht Teil dieses Read-Modells.</small>
          </div>
        </div>
      </header>

      <section class="filter-panel" aria-labelledby="filter-title">
        <div class="section-heading">
          <div><span class="step">01</span><h2 id="filter-title">Scope waehlen</h2></div>
          <button type="button" class="secondary" (click)="load(true)" [disabled]="loading">Snapshot laden</button>
        </div>
        <div class="filters">
          <label>Tenant-Scope<input [(ngModel)]="tenantRef" autocomplete="off" maxlength="128" /></label>
          <label>Region<input [(ngModel)]="region" autocomplete="off" maxlength="128" /></label>
          <label>Room-Scope<input [(ngModel)]="roomFilter" autocomplete="off" maxlength="128" /></label>
          <label>Seitengroesse<input type="number" [(ngModel)]="pageSize" min="1" max="100" inputmode="numeric" /></label>
        </div>
        <p class="privacy-note">Filter werden nur an den authentifizierten Hub gesendet und nicht in Karten oder Browserlogs gespiegelt.</p>
      </section>

      <p class="status-line" aria-live="polite" role="status">
        <strong>Status:</strong> <code>{{ statusReason }}</code>
      </p>

      @if (loading) {
        <section class="empty-state" aria-busy="true"><h2>Snapshot wird geladen</h2><p>Die Anfrage ist abbrechbar und wird nicht automatisch wiederholt.</p></section>
      } @else if (page && page.items.length === 0) {
        <section class="empty-state"><h2>Keine freigegebenen Aggregate</h2><p>Kleine Kohorten koennen gemaess OBS-001 unterdrueckt sein.</p></section>
      } @else if (page) {
        <section aria-labelledby="snapshot-title">
          <div class="section-heading snapshot-heading">
            <div><span class="step">02</span><h2 id="snapshot-title">Diagnose-Snapshot</h2></div>
            <span class="reason"><span aria-hidden="true">R</span> {{ page.reasonCode }}</span>
          </div>
          <div class="diagnostic-grid">
            @for (item of page.items; track item.roomDiagnosticRef || $index) {
              <article class="diagnostic-card">
                <header>
                  <div>
                    <span class="card-index">ROOM {{ pad($index + 1) }}</span>
                    <h3>{{ shortRef(item.roomDiagnosticRef) }}</h3>
                  </div>
                  <span class="state-chip">{{ display(item.gateState) }}</span>
                </header>
                <dl>
                  <div><dt>Route</dt><dd>{{ display(item.routeStatus) }}</dd></div>
                  <div><dt>Health</dt><dd>{{ display(item.health) }}</dd></div>
                  <div><dt>Topology</dt><dd>{{ display(item.topology) }}</dd></div>
                  <div><dt>Epoch</dt><dd>{{ display(item.epochClass) }}</dd></div>
                  <div><dt>Layer req/allow/effective</dt><dd>{{ layerPath(item) }}</dd></div>
                  <div><dt>Queue / Drop</dt><dd>{{ display(item.queue.depthBucket) }} / {{ display(item.queue.dropReason) }}</dd></div>
                  <div><dt>Ingress / Egress / TURN</dt><dd>{{ trafficPath(item) }}</dd></div>
                  <div><dt>Rekey / Failover</dt><dd>{{ display(item.rekeyStatus) }} / {{ display(item.failoverStatus) }}</dd></div>
                  <div><dt>Capacity</dt><dd>{{ display(item.capacityProfile) }}</dd></div>
                  <div><dt>Evidence</dt><dd>{{ evidenceLabel(item) }}</dd></div>
                </dl>
              </article>
            }
          </div>
          @if (page.nextCursor) {
            <button type="button" class="secondary next" (click)="load(false)" [disabled]="loading">Naechste Snapshot-Seite</button>
          }
        </section>
      }

      <section class="command-panel" aria-labelledby="command-title">
        <div class="section-heading">
          <div><span class="step danger-step">03</span><h2 id="command-title">Bestaetigtes Hub-Command</h2></div>
          <span class="mutation-boundary">Keine optimistische Mutation</span>
        </div>
        <p>Die originale Room-Referenz wird verdeckt eingegeben, nicht angezeigt und nach Erfolg geloescht.</p>
        <div class="command-fields">
          <label>Room-Referenz<input type="password" [(ngModel)]="commandRoomRef" autocomplete="off" maxlength="128" /></label>
          <label>Expected Version<input type="number" [(ngModel)]="expectedVersion" min="0" inputmode="numeric" /></label>
          <label>Quality
            <select [(ngModel)]="qualityPreference">
              <option value="auto">auto</option><option value="low">low</option>
              <option value="medium">medium</option><option value="high">high</option>
            </select>
          </label>
          <label class="toggle"><input type="checkbox" [(ngModel)]="dataSaver" /> Data Saver</label>
          <label class="toggle"><input type="checkbox" [(ngModel)]="audioOnly" /> Audio only</label>
        </div>
        <div class="command-actions">
          <button type="button" class="start" (click)="prepare('start')">Start vorbereiten</button>
          <button type="button" class="secondary" (click)="prepare('set_preferences')">Preferences vorbereiten</button>
          <button type="button" class="stop" (click)="prepare('stop')">Stop vorbereiten</button>
        </div>

        @if (pendingCommand) {
          <section class="confirm" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
            <h3 id="confirm-title">Command {{ pendingCommand }} explizit freigeben</h3>
            <p>Der Hub prueft RBAC, Parent-Readiness, Capacity, Version und Kill-Switch erneut.</p>
            <label class="toggle"><input type="checkbox" [(ngModel)]="confirmationChecked" /> Auswirkungen verstanden</label>
            <label>Zur Bestaetigung <code>FREIGEBEN</code> eingeben
              <input [(ngModel)]="confirmationPhrase" autocomplete="off" maxlength="16" />
            </label>
            <div class="command-actions">
              <button type="button" class="stop" (click)="execute()" [disabled]="commandBusy">Bestaetigt an Hub senden</button>
              <button type="button" class="secondary" (click)="cancelCommand()" [disabled]="commandBusy">Abbrechen</button>
            </div>
          </section>
        }

        @if (lastCommand) {
          <div class="command-result" aria-live="polite">
            <strong>{{ lastCommand.accepted ? 'Hub hat bestaetigt' : 'Hub hat abgelehnt' }}</strong>
            <span>State {{ lastCommand.state }}</span>
            <span>Version {{ lastCommand.effectiveVersion }}</span>
            <code>{{ lastCommand.reasonCode }}</code>
            @if (lastCommand.replayed) { <span>Idempotenter Replay</span> }
          </div>
        }
      </section>
    </main>
  `,
  styles: [`
    :host { display:block; --ink:#15251f; --muted:#587066; --paper:#f6f2e7; --line:#c7d1c9; --moss:#23614d; --amber:#b56a16; --danger:#a8322d; }
    .sfb-ops { color:var(--ink); min-height:100%; padding:clamp(16px,3vw,40px); background:radial-gradient(circle at 88% 4%,rgba(181,106,22,.15),transparent 27%),repeating-linear-gradient(90deg,rgba(21,37,31,.035) 0 1px,transparent 1px 48px),var(--paper); font-family:"Aptos","Trebuchet MS",sans-serif; }
    h1,h2,h3,p { margin-top:0; } h1,h2,h3 { font-family:Georgia,"Times New Roman",serif; }
    h1 { max-width:800px; font-size:clamp(2.2rem,6vw,5.4rem); line-height:.92; letter-spacing:-.055em; margin-bottom:18px; }
    h2 { margin:0; font-size:clamp(1.35rem,3vw,2rem); } .hero { display:grid; grid-template-columns:minmax(0,1fr) minmax(260px,360px); gap:28px; align-items:end; padding-bottom:32px; border-bottom:3px solid var(--ink); }
    .eyebrow,.card-index { color:var(--amber); font-size:.75rem; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }
    .lede { max-width:680px; color:var(--muted); font-size:1.05rem; }
    .evidence { display:flex; gap:12px; padding:18px; background:rgba(255,255,255,.64); border:1px solid var(--line); box-shadow:7px 7px 0 rgba(21,37,31,.12); }
    .evidence small,.evidence strong { display:block; } .evidence small { margin-top:5px; color:var(--muted); }
    .signal { width:13px; height:13px; margin-top:4px; border:2px solid var(--ink); background:#d6cfc1; flex:0 0 auto; } .signal[data-state="known"] { background:#4b9a70; }
    .filter-panel,.command-panel { margin-top:30px; padding:clamp(18px,3vw,30px); background:rgba(255,255,255,.72); border:1px solid var(--line); }
    .section-heading { display:flex; justify-content:space-between; align-items:center; gap:20px; margin-bottom:22px; } .section-heading>div { display:flex; align-items:center; gap:12px; }
    .step { display:grid; place-items:center; width:34px; height:34px; color:#fff; background:var(--moss); font:700 .75rem/1 "Aptos",sans-serif; } .danger-step { background:var(--danger); }
    .filters,.command-fields { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; } .command-fields { grid-template-columns:2fr 1fr 1fr auto auto; align-items:end; }
    label { display:grid; gap:7px; color:var(--muted); font-size:.78rem; font-weight:800; letter-spacing:.04em; text-transform:uppercase; }
    input,select { width:100%; box-sizing:border-box; min-height:44px; padding:9px 11px; color:var(--ink); background:#fffef9; border:1px solid #879a90; border-radius:0; font:600 .95rem/1.2 "Aptos",sans-serif; }
    input:focus-visible,select:focus-visible,button:focus-visible { outline:3px solid #e39a39; outline-offset:3px; }
    .toggle { display:flex; align-items:center; gap:8px; min-height:44px; color:var(--ink); white-space:nowrap; } .toggle input { width:20px; min-height:20px; }
    button { min-height:44px; padding:10px 16px; border:2px solid var(--ink); border-radius:0; font-weight:800; cursor:pointer; } button:disabled { cursor:not-allowed; opacity:.5; }
    .secondary { color:var(--ink); background:transparent; } .start { color:#fff; background:var(--moss); } .stop { color:#fff; background:var(--danger); }
    .privacy-note,.status-line { color:var(--muted); } .privacy-note { margin:15px 0 0; font-size:.85rem; } .status-line { padding:13px 0; border-bottom:1px solid var(--line); }
    code { overflow-wrap:anywhere; } .empty-state { margin:26px 0; padding:38px; text-align:center; border:1px dashed #879a90; }
    .snapshot-heading { margin-top:30px; } .reason,.mutation-boundary { padding:7px 10px; color:var(--moss); border:1px solid currentColor; font:700 .75rem/1 "Aptos",sans-serif; }
    .diagnostic-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }
    .diagnostic-card { padding:22px; background:#fffef9; border:1px solid var(--line); box-shadow:5px 5px 0 rgba(35,97,77,.12); }
    .diagnostic-card header { display:flex; justify-content:space-between; align-items:start; gap:12px; padding-bottom:15px; border-bottom:1px solid var(--line); } .diagnostic-card h3 { margin:4px 0 0; }
    .state-chip { padding:6px 9px; color:var(--ink); background:#e8dfc8; border-left:4px solid var(--amber); font-size:.75rem; font-weight:800; }
    dl { display:grid; grid-template-columns:1fr 1fr; gap:0 18px; margin-bottom:0; } dl div { padding:11px 0; border-bottom:1px dotted var(--line); } dt { color:var(--muted); font-size:.72rem; font-weight:800; text-transform:uppercase; } dd { margin:4px 0 0; font-weight:700; overflow-wrap:anywhere; }
    .next { margin-top:18px; } .command-actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:20px; }
    .confirm { margin-top:22px; padding:22px; background:#fff4e3; border:3px solid var(--danger); box-shadow:7px 7px 0 rgba(168,50,45,.15); } .confirm label:not(.toggle) { margin-top:16px; max-width:420px; }
    .command-result { display:flex; flex-wrap:wrap; gap:10px 18px; align-items:center; margin-top:20px; padding:16px; border-left:6px solid var(--moss); background:#e7f0e9; }
    @media (max-width:900px) { .hero { grid-template-columns:1fr; } .filters { grid-template-columns:repeat(2,minmax(0,1fr)); } .command-fields { grid-template-columns:1fr 1fr; } .diagnostic-grid { grid-template-columns:1fr; } }
    @media (max-width:520px) { .sfb-ops { padding:14px; } h1 { font-size:2.45rem; } .filters,.command-fields,dl { grid-template-columns:1fr; } .section-heading { align-items:flex-start; flex-direction:column; } .section-heading button { width:100%; } .diagnostic-card { padding:16px; } .command-actions button { width:100%; } }
    @media (prefers-reduced-motion:reduce) { * { scroll-behavior:auto !important; transition:none !important; animation:none !important; } }
  `],
})
export class SfuBroadcastOperatorComponent implements OnInit, OnDestroy {
  private readonly api = inject(SfuBroadcastOperationsApiService);
  private readonly directory = inject(AgentDirectoryService);
  private readSubscription: Subscription | null = null;
  private readonly subscriptions = new Subscription();

  tenantRef = '';
  region = '';
  roomFilter = '';
  pageSize = 25;
  page: SfuBroadcastOperationsPage | null = null;
  loading = false;
  statusReason = 'sfu_operations_scope_required';

  commandRoomRef = '';
  expectedVersion = 0;
  qualityPreference: SfuBroadcastQualityPreference = 'auto';
  dataSaver = false;
  audioOnly = false;
  pendingCommand: SfuBroadcastCommandName | null = null;
  confirmationChecked = false;
  confirmationPhrase = '';
  commandBusy = false;
  private commandIdempotencyKey: string | null = null;
  lastCommand: SfuBroadcastCommandResult | null = null;

  ngOnInit(): void { this.statusReason = 'sfu_operations_scope_required'; }

  ngOnDestroy(): void {
    this.readSubscription?.unsubscribe();
    this.subscriptions.unsubscribe();
    this.commandRoomRef = '';
    this.confirmationPhrase = '';
    this.commandIdempotencyKey = null;
  }

  load(resetCursor: boolean): void {
    if (!this.tenantRef && !this.region && !this.roomFilter) {
      this.statusReason = 'sfu_operations_scope_required';
      this.page = null;
      return;
    }
    const hub = this.directory.list().find(value => value.role === 'hub');
    if (!hub) { this.statusReason = 'sfu_operations_hub_unavailable'; return; }
    this.readSubscription?.unsubscribe();
    this.loading = true;
    this.statusReason = 'sfu_operations_loading';
    this.readSubscription = this.api.read(hub.url, {
      tenantRef: this.tenantRef || undefined,
      region: this.region || undefined,
      roomRef: this.roomFilter || undefined,
      pageSize: this.pageSize,
      cursor: resetCursor ? undefined : this.page?.nextCursor ?? undefined,
    }).subscribe({
      next: page => {
        this.page = page;
        this.statusReason = page.reasonCode;
        this.loading = false;
      },
      error: error => {
        this.statusReason = httpReason(error, 'sfu_operations_read_failed');
        if (permissionReason(this.statusReason)) this.page = null;
        this.loading = false;
      },
    });
  }

  prepare(command: SfuBroadcastCommandName): void {
    this.pendingCommand = command;
    this.commandIdempotencyKey = createCommandIdempotencyKey();
    this.confirmationChecked = false;
    this.confirmationPhrase = '';
    this.lastCommand = null;
    this.statusReason = 'sfu_command_confirmation_required';
  }

  cancelCommand(): void {
    this.pendingCommand = null;
    this.commandIdempotencyKey = null;
    this.commandRoomRef = '';
    this.confirmationChecked = false;
    this.confirmationPhrase = '';
    this.statusReason = 'sfu_command_cancelled_locally';
  }

  execute(): void {
    const command = this.pendingCommand;
    if (this.commandBusy) return;
    if (!command || !this.confirmationChecked || this.confirmationPhrase !== 'FREIGEBEN') {
      this.statusReason = 'sfu_command_confirmation_required';
      return;
    }
    const hub = this.directory.list().find(value => value.role === 'hub');
    if (!hub) { this.statusReason = 'sfu_operations_hub_unavailable'; return; }
    this.commandBusy = true;
    this.statusReason = 'sfu_command_pending_hub_confirmation';
    const options = command === 'set_preferences'
      ? { dataSaver: this.dataSaver, audioOnly: this.audioOnly, qualityPreference: this.qualityPreference }
      : {};
    let operation;
    try {
      operation = this.api.command(hub.url, {
        roomRef: this.commandRoomRef,
        command,
        expectedVersion: this.expectedVersion,
        confirmed: true,
        options,
      }, this.commandIdempotencyKey ??= createCommandIdempotencyKey());
    } catch (error) {
      this.commandBusy = false;
      this.statusReason = localReason(error, 'sfu_command_payload_invalid');
      return;
    }
    this.subscriptions.add(operation.subscribe({
      next: result => {
        this.lastCommand = result;
        this.statusReason = result.reasonCode;
        this.expectedVersion = result.effectiveVersion;
        this.pendingCommand = null;
        this.commandIdempotencyKey = null;
        this.confirmationChecked = false;
        this.confirmationPhrase = '';
        this.commandRoomRef = '';
        this.commandBusy = false;
        if (result.accepted) this.load(true);
      },
      error: error => {
        this.statusReason = httpReason(error, 'sfu_command_failed');
        if (permissionReason(this.statusReason)) this.page = null;
        this.commandBusy = false;
      },
    }));
  }

  display(value: SfuDiagnosticAtom): string {
    if (value === null) return 'suppressed';
    if (typeof value === 'boolean') return value ? 'yes' : 'no';
    return String(value);
  }

  shortRef(value: string | null): string {
    if (!value) return 'suppressed';
    return value.length <= 20 ? value : `${value.slice(0, 8)}...${value.slice(-6)}`;
  }

  pad(value: number): string { return String(value).padStart(2, '0'); }

  layerPath(item: SfuBroadcastOperationItem): string {
    return [item.layers.requested, item.layers.allowed, item.layers.effective].map(value => this.display(value)).join(' / ');
  }

  trafficPath(item: SfuBroadcastOperationItem): string {
    return [item.traffic.ingressBucket, item.traffic.egressBucket, item.traffic.turnBucket]
      .map(value => this.display(value)).join(' / ');
  }

  evidenceLabel(item: SfuBroadcastOperationItem): string {
    return item.gateState === null ? 'not_provided' : `gate:${this.display(item.gateState)}`;
  }
}

function httpReason(error: any, fallback: string): string {
  const candidate = error?.error?.reason_code ?? error?.error?.reasonCode ?? error?.message;
  return typeof candidate === 'string' && /^sfu_[a-z0-9_]{2,116}$/.test(candidate) ? candidate : fallback;
}

function localReason(error: unknown, fallback: string): string {
  return error instanceof Error && /^sfu_[a-z0-9_]{2,116}$/.test(error.message) ? error.message : fallback;
}

function createCommandIdempotencyKey(): string {
  return `sfb-command-${crypto.randomUUID()}`;
}

function permissionReason(value: string): boolean {
  return [
    'sfu_operations_tenant_forbidden', 'sfu_operations_region_forbidden',
    'sfu_command_room_forbidden', 'sfu_command_tenant_forbidden',
    'sfu_command_permission_revoked', 'sfu_command_role_forbidden',
  ].includes(value);
}
