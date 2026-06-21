import {
  Component,
  ChangeDetectionStrategy,
  OnInit,
  inject,
  signal,
  computed,
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { PolicyService } from '../services/policy.service';
import {
  ChPolicySnapshotReadModel,
  ChPolicyUpdateRequest,
  DEFAULT_SENSITIVE_FILE_PATTERNS,
} from '../models/codehug.models';

/**
 * PolicyPanelComponent — CodeHug-internes Policy-Edit-Panel (CH-010-004).
 *
 * Zeigt aktive Policies (Pfade, Sensitive-Patterns, Risk-Level, Tools).
 * Bearbeitung nur im Write-Modus; liest und schreibt via PolicyService (Hub-API).
 * Keine Code-Duplikation mit features/context-access-policy — selbe API, eigene UI.
 */
@Component({
  selector: 'ch-policy-panel',
  standalone: true,
  imports: [DatePipe, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="ch-pp">
      <header class="ch-pp-head">
        <div class="ch-pp-title-row">
          <h2 class="ch-pp-title">Policy</h2>
          @if (snapshot()) {
            <span class="ch-pp-version">v{{ snapshot()!.policyVersion }}</span>
          }
        </div>
        <p class="ch-pp-lead">
          CodeHug-relevante Policies: erlaubte Pfade, Sensitive-File-Muster,
          Risk-Level, Tools. Schreibzugriff nur im Write-Modus.
        </p>

        <!-- Write-Mode-Steuerung -->
        <div class="ch-pp-write-mode-bar" [attr.data-mode]="policy.writeMode()">
          <span class="ch-pp-wm-label">
            @if (policy.writeMode() === 'read-only') {
              <span class="ch-pp-wm-dot ch-pp-dot-ro"></span>
              Read-only — Änderungen blockiert
            } @else {
              <span class="ch-pp-wm-dot ch-pp-dot-wa"></span>
              Write-Mode aktiv
              @if (writeModeExpiresIn() !== null) {
                — läuft ab in {{ writeModeExpiresIn() }}s
              }
            }
          </span>
          @if (policy.writeMode() === 'read-only') {
            <button type="button" class="ch-btn ch-btn-warn" (click)="armWriteMode()">
              Write-Modus aktivieren
            </button>
          } @else {
            <button type="button" class="ch-btn ch-btn-secondary" (click)="disarmWriteMode()">
              Write-Modus beenden
            </button>
          }
        </div>
      </header>

      <!-- Fehler / Loading -->
      @if (loading()) {
        <p class="ch-muted ch-pp-loading">Lade Policy…</p>
      } @else if (loadError()) {
        <div class="ch-pp-error-box" role="alert">
          <strong>Fehler beim Laden:</strong> {{ loadError() }}
          <button type="button" class="ch-btn ch-btn-mini" (click)="load()">Nochmal</button>
        </div>
      } @else if (!snapshot()) {
        <p class="ch-muted">Kein Policy-Snapshot verfügbar. Hub erreichbar?</p>
      } @else {
        <!-- ── Risk Level ──────────────────────────────────────────────── -->
        <section class="ch-pp-card" aria-labelledby="ch-pp-risk-h">
          <h3 id="ch-pp-risk-h" class="ch-pp-card-title">Risk Level</h3>
          <div class="ch-pp-risk-badge" [attr.data-level]="snapshot()!.riskLevel">
            {{ snapshot()!.riskLevel | uppercase }}
          </div>
          <p class="ch-muted ch-pp-hint">
            Beeinflusst den Freigabe-Workflow: high = strenger Approval-Prozess.
          </p>
        </section>

        <!-- ── Erlaubte Pfade ─────────────────────────────────────────── -->
        <section class="ch-pp-card" aria-labelledby="ch-pp-allowed-h">
          <header class="ch-pp-card-head">
            <h3 id="ch-pp-allowed-h" class="ch-pp-card-title">Erlaubte Pfade</h3>
            @if (canEdit()) {
              <button type="button" class="ch-btn ch-btn-mini" (click)="addAllowedPath()">+ Hinzufügen</button>
            }
          </header>

          @if (editAllowedPaths().length === 0) {
            <p class="ch-muted">Keine Einschränkung (alle Pfade erlaubt).</p>
          } @else {
            <ul class="ch-pp-path-list">
              @for (p of editAllowedPaths(); track $index) {
                <li class="ch-pp-path-item">
                  @if (canEdit()) {
                    <input
                      type="text"
                      class="ch-input ch-input-inline"
                      [value]="p"
                      (change)="updateAllowedPath($index, $any($event.target).value)" />
                    <button
                      type="button"
                      class="ch-btn ch-btn-danger ch-btn-mini"
                      (click)="removeAllowedPath($index)"
                      aria-label="Pfad entfernen">✕</button>
                  } @else {
                    <code class="ch-pp-path-code">{{ p }}</code>
                  }
                </li>
              }
            </ul>
          }
        </section>

        <!-- ── Verbotene Pfade ────────────────────────────────────────── -->
        <section class="ch-pp-card" aria-labelledby="ch-pp-denied-h">
          <header class="ch-pp-card-head">
            <h3 id="ch-pp-denied-h" class="ch-pp-card-title">Verbotene Pfade</h3>
            @if (canEdit()) {
              <button type="button" class="ch-btn ch-btn-mini" (click)="addDeniedPath()">+ Hinzufügen</button>
            }
          </header>

          @if (editDeniedPaths().length === 0) {
            <p class="ch-muted">Keine explizit verbotenen Pfade.</p>
          } @else {
            <ul class="ch-pp-path-list">
              @for (p of editDeniedPaths(); track $index) {
                <li class="ch-pp-path-item ch-pp-denied">
                  @if (canEdit()) {
                    <input
                      type="text"
                      class="ch-input ch-input-inline"
                      [value]="p"
                      (change)="updateDeniedPath($index, $any($event.target).value)" />
                    <button
                      type="button"
                      class="ch-btn ch-btn-danger ch-btn-mini"
                      (click)="removeDeniedPath($index)"
                      aria-label="Pfad entfernen">✕</button>
                  } @else {
                    <code class="ch-pp-path-code">{{ p }}</code>
                  }
                </li>
              }
            </ul>
          }
        </section>

        <!-- ── Sensitive-File-Patterns ───────────────────────────────── -->
        <section class="ch-pp-card" aria-labelledby="ch-pp-sensitive-h">
          <header class="ch-pp-card-head">
            <h3 id="ch-pp-sensitive-h" class="ch-pp-card-title">Sensitive-File-Muster</h3>
            @if (canEdit()) {
              <button type="button" class="ch-btn ch-btn-mini" (click)="addSensitivePattern()">+ Hinzufügen</button>
              <button type="button" class="ch-btn ch-btn-mini" (click)="resetSensitiveToDefaults()" title="Defaults wiederherstellen">
                Defaults
              </button>
            }
          </header>
          <p class="ch-muted ch-pp-hint">
            Dateien die diesen Mustern entsprechen werden erkannt, markiert und erfordern
            explizite Nutzer-Freigabe vor Aufnahme in ein Kontextpaket.
          </p>

          <ul class="ch-pp-path-list ch-pp-patterns">
            @for (pat of editSensitivePatterns(); track $index) {
              <li class="ch-pp-path-item ch-pp-sensitive">
                @if (canEdit()) {
                  <input
                    type="text"
                    class="ch-input ch-input-inline ch-mono"
                    [value]="pat"
                    (change)="updateSensitivePattern($index, $any($event.target).value)" />
                  <button
                    type="button"
                    class="ch-btn ch-btn-danger ch-btn-mini"
                    (click)="removeSensitivePattern($index)"
                    aria-label="Muster entfernen">✕</button>
                } @else {
                  <code class="ch-pp-path-code ch-mono">{{ pat }}</code>
                }
              </li>
            }
          </ul>
        </section>

        <!-- ── Tools ─────────────────────────────────────────────────── -->
        <section class="ch-pp-card" aria-labelledby="ch-pp-tools-h">
          <h3 id="ch-pp-tools-h" class="ch-pp-card-title">Tools</h3>
          <div class="ch-pp-tools-grid">
            <div>
              <p class="ch-pp-tools-label">Erlaubt</p>
              @if (snapshot()!.allowedTools.length === 0) {
                <p class="ch-muted">—</p>
              } @else {
                <ul class="ch-pp-tool-list">
                  @for (t of snapshot()!.allowedTools; track t) {
                    <li class="ch-tag ch-tag-allow">{{ t }}</li>
                  }
                </ul>
              }
            </div>
            <div>
              <p class="ch-pp-tools-label">Verboten</p>
              @if (snapshot()!.deniedTools.length === 0) {
                <p class="ch-muted">—</p>
              } @else {
                <ul class="ch-pp-tool-list">
                  @for (t of snapshot()!.deniedTools; track t) {
                    <li class="ch-tag ch-tag-deny">{{ t }}</li>
                  }
                </ul>
              }
            </div>
          </div>
        </section>

        <!-- ── Sonstiges ──────────────────────────────────────────────── -->
        <section class="ch-pp-card ch-pp-meta-card">
          <h3 class="ch-pp-card-title">Systemgrenzen</h3>
          <dl class="ch-pp-meta">
            <dt>Runtime Boundary</dt>
            <dd><span class="ch-tag">{{ snapshot()!.runtimeBoundary }}</span></dd>
            <dt>Cloud erlaubt</dt>
            <dd>{{ snapshot()!.cloudAllowed ? 'Ja' : 'Nein' }}</dd>
            <dt>Menschliche Freigabe</dt>
            <dd>{{ snapshot()!.requiresHumanApproval ? 'Immer erforderlich' : 'Nicht standardmäßig' }}</dd>
            @if (snapshot()!.approvalReason) {
              <dt>Freigabe-Grund</dt>
              <dd>{{ snapshot()!.approvalReason }}</dd>
            }
            <dt>Snapshot erstellt</dt>
            <dd>{{ snapshot()!.createdAt | date:'medium' }}</dd>
          </dl>
        </section>

        <!-- ── Speichern ──────────────────────────────────────────────── -->
        @if (canEdit() && hasUnsavedChanges()) {
          <div class="ch-pp-save-bar">
            <span class="ch-pp-unsaved-hint">Ungespeicherte Änderungen</span>
            <button
              type="button"
              class="ch-btn ch-btn-secondary"
              (click)="resetEdits()"
              [disabled]="saving()">
              Verwerfen
            </button>
            <button
              type="button"
              class="ch-btn ch-btn-primary"
              (click)="save()"
              [disabled]="saving()">
              {{ saving() ? 'Speichern…' : 'Änderungen speichern' }}
            </button>
          </div>
        }
        @if (saveError()) {
          <p class="ch-error" role="alert">{{ saveError() }}</p>
        }
        @if (saveSuccess()) {
          <p class="ch-success">Gespeichert.</p>
        }

        <!-- ── Audit-Log ──────────────────────────────────────────────── -->
        @if (policy.auditLog().length > 0) {
          <section class="ch-pp-card" aria-labelledby="ch-pp-audit-h">
            <header class="ch-pp-card-head">
              <h3 id="ch-pp-audit-h" class="ch-pp-card-title">Audit-Log</h3>
              <button type="button" class="ch-btn ch-btn-mini" (click)="policy.clearAudit()">Leeren</button>
            </header>
            <ul class="ch-pp-audit-list">
              @for (entry of policy.auditLog().slice(0, 50); track entry.id) {
                <li class="ch-pp-audit-item" [attr.data-decision]="entry.decision ?? 'info'">
                  <span class="ch-pp-audit-kind">{{ entry.kind }}</span>
                  <span class="ch-pp-audit-action">{{ entry.action }}</span>
                  @if (entry.decision) {
                    <span class="ch-pp-audit-decision ch-tag" [attr.data-decision]="entry.decision">
                      {{ entry.decision }}
                    </span>
                  }
                  @if (entry.reason) {
                    <span class="ch-pp-audit-reason">{{ entry.reason }}</span>
                  }
                  <time class="ch-pp-audit-time">{{ entry.ts | date:'HH:mm:ss' }}</time>
                </li>
              }
            </ul>
          </section>
        }
      }
    </section>
  `,
  styles: [`
    :host { display: block; padding: 14px; max-width: 900px; }
    .ch-pp { display: grid; gap: 14px; }

    .ch-pp-head { display: grid; gap: 8px; margin-bottom: 4px; }
    .ch-pp-title-row { display: flex; align-items: center; gap: 10px; }
    .ch-pp-title { margin: 0; font-size: 20px; }
    .ch-pp-version {
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      font-size: 11px;
      color: var(--muted);
    }
    .ch-pp-lead { margin: 0; font-size: 13px; color: var(--muted); }

    .ch-pp-write-mode-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 12px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card-bg);
      font-size: 12px;
    }
    .ch-pp-write-mode-bar[data-mode="write-armed"] {
      background: color-mix(in srgb, #f59e0b 12%, transparent);
      border-color: #f59e0b;
    }
    .ch-pp-wm-label { display: flex; align-items: center; gap: 8px; }
    .ch-pp-wm-dot {
      width: 8px; height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .ch-pp-dot-ro { background: var(--muted); }
    .ch-pp-dot-wa { background: #f59e0b; box-shadow: 0 0 6px #f59e0b; }

    .ch-pp-loading { animation: ch-pulse 1.4s ease-in-out infinite; }
    @keyframes ch-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

    .ch-pp-error-box {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border-radius: 8px;
      background: color-mix(in srgb, #b91c1c 12%, transparent);
      border: 1px solid #b91c1c;
      font-size: 13px;
      color: #b91c1c;
    }

    .ch-pp-card {
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 14px;
      background: var(--card-bg);
      display: grid;
      gap: 8px;
    }
    .ch-pp-card-head {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .ch-pp-card-title { margin: 0; font-size: 13px; font-weight: 600; flex: 1; }
    .ch-pp-hint { font-size: 11px; color: var(--muted); margin: 0; }

    .ch-pp-risk-badge {
      display: inline-block;
      padding: 4px 14px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.8px;
    }
    .ch-pp-risk-badge[data-level="low"] { background: color-mix(in srgb, #16a34a 18%, transparent); color: #14532d; }
    .ch-pp-risk-badge[data-level="medium"] { background: color-mix(in srgb, #f59e0b 22%, transparent); color: #78350f; }
    .ch-pp-risk-badge[data-level="high"] { background: color-mix(in srgb, #dc2626 18%, transparent); color: #7f1d1d; }

    .ch-pp-path-list {
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 4px;
    }
    .ch-pp-path-item {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .ch-pp-path-code {
      font-family: var(--mono, ui-monospace, monospace);
      font-size: 12px;
      padding: 2px 8px;
      border-radius: 4px;
      background: var(--bg);
      border: 1px solid var(--border);
    }
    .ch-pp-denied .ch-pp-path-code {
      background: color-mix(in srgb, #dc2626 8%, transparent);
      border-color: color-mix(in srgb, #dc2626 40%, transparent);
    }
    .ch-pp-sensitive .ch-pp-path-code {
      background: color-mix(in srgb, #f59e0b 10%, transparent);
      border-color: color-mix(in srgb, #f59e0b 50%, transparent);
    }

    .ch-input {
      padding: 4px 8px;
      border: 1px solid var(--border);
      border-radius: 5px;
      background: var(--bg);
      color: var(--fg);
      font-size: 12px;
    }
    .ch-input-inline { flex: 1; }

    .ch-btn {
      padding: 5px 10px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 12px;
      white-space: nowrap;
    }
    .ch-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .ch-btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    .ch-btn-secondary { background: var(--card-bg); }
    .ch-btn-warn { background: #f59e0b; color: #fff; border-color: #d97706; }
    .ch-btn-danger { background: color-mix(in srgb, #dc2626 14%, transparent); color: #b91c1c; border-color: #dc2626; }
    .ch-btn-mini { padding: 2px 7px; font-size: 11px; }

    .ch-pp-tools-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .ch-pp-tools-label { font-size: 11px; color: var(--muted); margin: 0 0 6px; text-transform: uppercase; letter-spacing: 0.5px; }
    .ch-pp-tool-list { list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 4px; }
    .ch-tag {
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      border: 1px solid var(--border);
      background: var(--bg);
    }
    .ch-tag-allow { background: color-mix(in srgb, #16a34a 12%, transparent); border-color: #16a34a; color: #14532d; }
    .ch-tag-deny { background: color-mix(in srgb, #dc2626 10%, transparent); border-color: #dc2626; color: #7f1d1d; }

    .ch-pp-meta { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; margin: 0; font-size: 12px; }
    .ch-pp-meta dt { color: var(--muted); font-weight: 500; }
    .ch-pp-meta dd { margin: 0; }

    .ch-pp-save-bar {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      padding: 10px 14px;
      border: 1px solid #f59e0b;
      border-radius: 8px;
      background: color-mix(in srgb, #f59e0b 8%, transparent);
    }
    .ch-pp-unsaved-hint { font-size: 12px; color: #92400e; flex: 1; }

    .ch-error { color: #b91c1c; font-size: 12px; margin: 4px 0; }
    .ch-success { color: #065f46; font-size: 12px; margin: 4px 0; }
    .ch-muted { color: var(--muted); font-size: 12px; margin: 0; }

    .ch-pp-audit-list {
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 3px;
      max-height: 240px;
      overflow: auto;
    }
    .ch-pp-audit-item {
      display: grid;
      grid-template-columns: 100px 1fr max-content 2fr max-content;
      gap: 8px;
      align-items: center;
      padding: 3px 6px;
      border-radius: 4px;
      font-size: 11px;
      background: var(--bg);
    }
    .ch-pp-audit-kind { color: var(--muted); font-weight: 500; }
    .ch-pp-audit-action { font-family: var(--mono, ui-monospace, monospace); }
    .ch-pp-audit-decision[data-decision="allow"] { background: color-mix(in srgb, #16a34a 12%, transparent); color: #14532d; border-color: #16a34a; }
    .ch-pp-audit-decision[data-decision="deny"] { background: color-mix(in srgb, #dc2626 10%, transparent); color: #7f1d1d; border-color: #dc2626; }
    .ch-pp-audit-decision[data-decision="require_approval"] { background: color-mix(in srgb, #f59e0b 14%, transparent); color: #78350f; border-color: #f59e0b; }
    .ch-pp-audit-reason { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ch-pp-audit-time { color: var(--muted); white-space: nowrap; }
    .ch-mono { font-family: var(--mono, ui-monospace, monospace); }
  `],
})
export class PolicyPanelComponent implements OnInit {
  readonly policy = inject(PolicyService);

  readonly snapshot = signal<ChPolicySnapshotReadModel | null>(null);
  readonly loading = signal(false);
  readonly loadError = signal<string | null>(null);
  readonly saving = signal(false);
  readonly saveError = signal<string | null>(null);
  readonly saveSuccess = signal(false);

  readonly editAllowedPaths = signal<string[]>([]);
  readonly editDeniedPaths = signal<string[]>([]);
  readonly editSensitivePatterns = signal<string[]>([]);

  readonly canEdit = computed(() => this.policy.writeModeActive());

  readonly hasUnsavedChanges = computed(() => {
    const snap = this.snapshot();
    if (!snap) return false;
    return (
      JSON.stringify(this.editAllowedPaths()) !== JSON.stringify(snap.allowedPaths) ||
      JSON.stringify(this.editDeniedPaths()) !== JSON.stringify(snap.deniedPaths) ||
      JSON.stringify(this.editSensitivePatterns()) !== JSON.stringify(snap.sensitiveFilePatterns)
    );
  });

  readonly writeModeExpiresIn = computed(() => {
    const exp = this.policy.writeModeExpiresAt();
    if (!exp) return null;
    return Math.max(0, Math.round((exp - Date.now()) / 1000));
  });

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.loadError.set(null);
    this.policy.loadCurrentSnapshot().subscribe({
      next: snap => {
        this.snapshot.set(snap);
        this.resetEdits();
        this.loading.set(false);
      },
      error: (err: Error) => {
        this.loadError.set(err.message);
        this.loading.set(false);
      },
    });
  }

  armWriteMode(): void {
    const ok = confirm('Write-Modus aktivieren? Änderungen an Policies können das Systemverhalten direkt beeinflussen.');
    if (ok) {
      this.policy.armWriteMode();
      this.policy.appendAudit({ kind: 'write-armed', action: 'policy-panel:arm-write-mode' });
    }
  }

  disarmWriteMode(): void {
    this.policy.disarmWriteMode();
    this.policy.appendAudit({ kind: 'write-disarmed', action: 'policy-panel:disarm-write-mode' });
    this.resetEdits();
  }

  resetEdits(): void {
    const snap = this.snapshot();
    if (!snap) return;
    this.editAllowedPaths.set([...snap.allowedPaths]);
    this.editDeniedPaths.set([...snap.deniedPaths]);
    this.editSensitivePatterns.set([...snap.sensitiveFilePatterns]);
    this.saveSuccess.set(false);
    this.saveError.set(null);
  }

  // ── Allowed Paths ──────────────────────────────────────────────────────
  addAllowedPath(): void { this.editAllowedPaths.update(a => [...a, '']); }
  removeAllowedPath(i: number): void { this.editAllowedPaths.update(a => a.filter((_, idx) => idx !== i)); }
  updateAllowedPath(i: number, v: string): void { this.editAllowedPaths.update(a => a.map((x, idx) => idx === i ? v : x)); }

  // ── Denied Paths ───────────────────────────────────────────────────────
  addDeniedPath(): void { this.editDeniedPaths.update(a => [...a, '']); }
  removeDeniedPath(i: number): void { this.editDeniedPaths.update(a => a.filter((_, idx) => idx !== i)); }
  updateDeniedPath(i: number, v: string): void { this.editDeniedPaths.update(a => a.map((x, idx) => idx === i ? v : x)); }

  // ── Sensitive Patterns ─────────────────────────────────────────────────
  addSensitivePattern(): void { this.editSensitivePatterns.update(a => [...a, '']); }
  removeSensitivePattern(i: number): void { this.editSensitivePatterns.update(a => a.filter((_, idx) => idx !== i)); }
  updateSensitivePattern(i: number, v: string): void { this.editSensitivePatterns.update(a => a.map((x, idx) => idx === i ? v : x)); }
  resetSensitiveToDefaults(): void { this.editSensitivePatterns.set([...DEFAULT_SENSITIVE_FILE_PATTERNS]); }

  // ── Save ───────────────────────────────────────────────────────────────
  async save(): Promise<void> {
    if (!this.policy.ensureWriteModeValid()) {
      this.saveError.set('Write-Modus ist abgelaufen. Bitte neu aktivieren.');
      return;
    }
    this.saving.set(true);
    this.saveError.set(null);
    this.saveSuccess.set(false);

    const req: ChPolicyUpdateRequest = {
      allowedPaths: this.editAllowedPaths().filter(p => p.trim()),
      deniedPaths: this.editDeniedPaths().filter(p => p.trim()),
      sensitiveFilePatterns: this.editSensitivePatterns().filter(p => p.trim()),
    };

    try {
      const updated = await firstValueFrom(this.policy.update(req));
      this.snapshot.set(updated);
      this.resetEdits();
      this.saveSuccess.set(true);
      this.policy.appendAudit({
        kind: 'approval',
        action: 'policy-panel:save',
        decision: 'allow',
        reason: 'Policy manuell geändert',
      });
    } catch (err: unknown) {
      this.saveError.set(err instanceof Error ? err.message : 'Speichern fehlgeschlagen');
    } finally {
      this.saving.set(false);
    }
  }
}
