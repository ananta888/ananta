import { Component, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { AdminFacade } from '../features/admin/admin.facade';

@Component({
  standalone: true,
  selector: 'app-wikipedia',
  imports: [CommonModule, FormsModule],
  styles: [`
    .card { border: 1px solid var(--border); border-radius: 12px; padding: 18px; background: var(--card-bg); }
    .card + .card { margin-top: 16px; }
    .row { display: flex; align-items: center; gap: 12px; }
    .space-between { justify-content: space-between; }
    .mt-sm { margin-top: 10px; }
    .mt-md { margin-top: 18px; }
    .muted { color: var(--muted, #666); }
    .font-sm { font-size: 12px; }
    .no-margin { margin: 0; }
    .title-muted { font-size: 13px; margin: 4px 0 0; }
    .flex-1 { flex: 1; min-width: 0; }

    .upload-row { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; }
    .checkbox-inline { display: flex; align-items: center; gap: 8px; font-size: 13px; }
    .label-no-margin { display: flex; flex-direction: column; gap: 4px; font-size: 13px; font-weight: 500; }
    .label-no-margin input, .label-no-margin select { margin-top: 2px; }

    .pill { display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 10px;
            background: rgba(0,0,0,0.06); font-size: 12px; }
    .pills { display: flex; flex-wrap: wrap; gap: 8px; }

    /* job card */
    .wiki-job-card { border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px;
                     background: color-mix(in srgb, var(--card-bg) 92%, transparent); }
    .wiki-job-header { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
                       font-size: 13px; font-weight: 500; }
    .wiki-job-phase { flex: 1; }
    .wiki-progress-bar { height: 8px; border-radius: 4px; background: var(--border, #ddd);
                         overflow: hidden; margin-top: 10px; }
    .wiki-progress-fill { height: 100%; border-radius: 4px; background: var(--primary-color, #2563eb);
                          transition: width 0.5s ease; }
    .wiki-progress-fill.done { background: #16a34a; }
    .wiki-progress-fill.fail { background: #dc2626; }
    .wiki-steps { display: flex; flex-direction: column; gap: 6px; }
    .wiki-step { display: flex; align-items: center; gap: 8px; font-size: 12px;
                 color: var(--muted, #888); padding: 5px 8px; border-radius: 6px; }
    .wiki-step.active { color: var(--primary-color, #2563eb);
                        background: color-mix(in srgb, var(--primary-color,#2563eb) 8%,transparent);
                        font-weight: 600; }
    .wiki-step.done { color: #16a34a; }
    .wiki-step-icon { font-weight: 700; font-size: 13px; }
    .danger { color: #dc2626; }
  `],
  template: `
    @if (hasActiveJob() && !wikiImportJobId) {
      <div class="card" style="border-color:#f59e0b; background:color-mix(in srgb,#fef3c7 60%,var(--card-bg))">
        <div class="row space-between">
          <div>
            <strong>Ein Import läuft bereits</strong>
            <div class="muted font-sm mt-5">Job {{ activeBlockingJob()?.job_id }} — {{ jobPhaseLabel(activeBlockingJob()) }}</div>
          </div>
          <div class="row">
            <button class="secondary" (click)="loadAllJobs()">Aktualisieren</button>
            <button class="secondary danger" (click)="cancelOtherJob(activeBlockingJob()?.job_id)">Abbrechen</button>
          </div>
        </div>
      </div>
    }

    <div class="card mt-md">
      <div class="row space-between">
        <div>
          <h2 class="no-margin">Wikipedia</h2>
          <p class="muted title-muted">Wikimedia-Dump herunterladen und als lokale RAG-Quelle indizieren.</p>
        </div>
        <button class="secondary" (click)="loadWikiPresets()" [disabled]="loadingWikiPresets || wikiImportBusy">
          Presets neu laden
        </button>
      </div>

      <div class="upload-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Preset
            <select [(ngModel)]="selectedWikiPresetId" (ngModelChange)="onWikiPresetChanged()">
              <option value="">Eigene URL</option>
              @for (preset of wikiPresets; track preset.id) {
                <option [value]="preset.id" [disabled]="preset.supported === false">{{ preset.label }}</option>
              }
            </select>
          </label>
        </div>
        <div class="flex-1">
          <label class="label-no-margin">Dump URL
            <input [(ngModel)]="wikiCorpusUrl" placeholder="https://.../pages-articles-multistream.xml.bz2"
                   [disabled]="!!selectedWikiPresetId" />
          </label>
        </div>
      </div>

      <div class="upload-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Source ID
            <input [(ngModel)]="wikiSourceId" placeholder="z.B. wikipedia-de-multistream-latest" />
          </label>
        </div>
        <div class="flex-1">
          <label class="label-no-margin">Sprache
            <input [(ngModel)]="wikiLanguage" placeholder="de / en" style="max-width:80px" />
          </label>
        </div>
        <label class="label-no-margin">
          <span class="muted font-sm">Indizierungs-Profil</span>
          <select [(ngModel)]="wikiProfileName">
            @for (p of knowledgeProfiles; track p.name) {
              <option [value]="p.name">{{ p.label }}</option>
            }
          </select>
        </label>
      </div>

      <div class="upload-row mt-sm">
        <label class="checkbox-inline">
          <input type="checkbox" [(ngModel)]="wikiCodeCompassPrerender" />
          CodeCompass Vor-Rendering
        </label>
        <label class="checkbox-inline">
          <input type="checkbox" [(ngModel)]="wikiStrict" />
          Strikter Import
        </label>
        <button (click)="importWiki()" [disabled]="wikiImportBusy || !canImportWiki()">
          {{ wikiImportBusy ? 'Importiere…' : 'Wikipedia importieren' }}
        </button>
      </div>

      @if (selectedWikiPreset()) {
        <div class="pills mt-sm">
          <span class="pill">{{ selectedWikiPreset()?.size_hint || 'Größe unbekannt' }}</span>
          @if (selectedWikiPreset()?.index_url) { <span class="pill">Multistream-Index</span> }
          @if (selectedWikiPreset()?.supported === false) {
            <span class="pill">Parser noch nicht unterstützt</span>
          }
        </div>
        @if (wikiImportSafetyHint()) {
          <div class="muted font-sm mt-sm">{{ wikiImportSafetyHint() }}</div>
        }
      }
    </div>

    @if (wikiImportJobId || wikiImportJob) {
      <div class="card mt-md">
        <div class="wiki-job-card">
          <div class="wiki-job-header">
            <span class="wiki-job-phase">{{ wikiPhaseLabel() }}</span>
            <span class="pill">{{ wikiProgressPercent() }}%</span>
            <span class="muted font-sm">Job: {{ wikiImportJobId || wikiImportJob?.job_id }}</span>
          </div>

          <div class="wiki-progress-bar">
            <div class="wiki-progress-fill"
                 [style.width.%]="wikiProgressPercent()"
                 [class.done]="wikiJobStatus() === 'completed'"
                 [class.fail]="wikiJobStatus() === 'failed' || wikiJobStatus() === 'cancelled'">
            </div>
          </div>

          <div class="wiki-steps mt-sm">
            <div class="wiki-step"
                 [class.active]="wikiImportJob?.phase === 'download_parse_normalize'"
                 [class.done]="wikiProgressPercent() >= 75 || wikiJobStatus() === 'completed'">
              <span class="wiki-step-icon">①</span>
              <span>Download &amp; Parse</span>
              @if (wikiImportJob?.phase === 'download_parse_normalize') {
                @if (wikiImportJob?.parse_items_done) {
                  <span class="muted font-sm">&nbsp;— {{ wikiImportJob.parse_items_done | number }} Artikel verarbeitet</span>
                } @else {
                  <span class="muted font-sm">&nbsp;— läuft…</span>
                }
              }
            </div>
            <div class="wiki-step"
                 [class.active]="wikiImportJob?.phase === 'index'"
                 [class.done]="wikiJobStatus() === 'completed'">
              <span class="wiki-step-icon">②</span>
              <span>CodeCompass-Indexierung</span>
              @if (wikiImportJob?.phase === 'index') {
                <span class="muted font-sm">&nbsp;— läuft…</span>
              }
            </div>
            <div class="wiki-step" [class.done]="wikiJobStatus() === 'completed'">
              <span class="wiki-step-icon">③</span>
              <span>Bereit für RAG</span>
            </div>
          </div>

          @if (wikiImportStats()) {
            <div class="pills mt-sm">
              <span class="pill">Source: {{ wikiImportStats()!.source_id || wikiSourceId }}</span>
              <span class="pill">{{ wikiImportStats()!.records }} Records</span>
              @if (wikiImportStats()!.issues > 0) {
                <span class="pill">{{ wikiImportStats()!.issues }} Issues</span>
              }
            </div>
          }

          <div class="upload-row mt-sm">
            @if (canPauseWikiJob()) {
              <button class="secondary" (click)="pauseWikiJob()" [disabled]="wikiJobControlBusy">Pausieren</button>
            }
            @if (canResumeWikiJob()) {
              <button class="secondary" (click)="resumeWikiJob()" [disabled]="wikiJobControlBusy">Fortsetzen</button>
            }
            @if (canCancelWikiJob()) {
              <button class="secondary" (click)="cancelWikiJob()" [disabled]="wikiJobControlBusy">Abbrechen</button>
            }
            @if (canRetryWikiImport()) {
              <button (click)="retryWikiImport()">Erneut versuchen</button>
            }
          </div>

          @if (wikiJobStatus() === 'failed') {
            <div class="muted font-sm mt-sm danger">
              Fehler: {{ wikiImportJob?.error || 'unbekannter Fehler' }}
            </div>
          }
        </div>
      </div>
    }
  `,
})
export class WikipediaComponent implements OnDestroy {
  private dir    = inject(AgentDirectoryService);
  private hubApi = inject(AdminFacade);
  private ns     = inject(NotificationService);

  hub = this.dir.list().find((a) => a.role === 'hub');

  // ── profiles ────────────────────────────────────────────────────────────────
  knowledgeProfiles: any[] = [];
  wikiProfileName = 'default';

  // ── presets ─────────────────────────────────────────────────────────────────
  readonly fallbackWikiPresets: any[] = [
    {
      id: 'wikipedia-de-multistream-latest',
      label: 'Wikipedia DE: Artikel Multistream (latest)',
      description: 'Empfohlen fuer ernsthaftes deutsches RAG: echter Wikimedia XML.BZ2 Multistream-Dump plus Index.',
      corpus_url: 'https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles-multistream.xml.bz2',
      index_url: 'https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles-multistream-index.txt.bz2',
      source_id: 'wikipedia-de-multistream-latest',
      language: 'de',
      size_hint: '~8.1 GB dump + ~63 MB index',
      recommended: true,
      import_format: 'mediawiki-multistream',
      codecompass_prerender: true,
    },
    {
      id: 'wikipedia-de-pages-latest',
      label: 'Wikipedia DE: Artikel nicht-Multistream (latest)',
      description: 'Fallback ohne Multistream-Index.',
      corpus_url: 'https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles.xml.bz2',
      source_id: 'wikipedia-de-pages-latest',
      language: 'de',
      size_hint: '~7.8 GB',
      recommended: false,
      import_format: 'mediawiki-xml',
      codecompass_prerender: true,
    },
    {
      id: 'wikipedia-de-zim-mini-2026-04',
      label: 'Wikipedia DE: ZIM mini 2026-04 (Prototyp)',
      description: 'Kiwix/ZIM – Parser noch nicht aktiv.',
      corpus_url: 'https://dumps.wikimedia.org/kiwix/zim/wikipedia/wikipedia_de_all_mini_2026-04.zim',
      source_id: 'wikipedia-de-zim-mini-2026-04',
      language: 'de',
      size_hint: '~3.9 GiB',
      recommended: false,
      import_format: 'zim',
      supported: false,
      codecompass_prerender: false,
    },
    {
      id: 'wikipedia-de-zim-nopic-2026-01',
      label: 'Wikipedia DE: ZIM ohne Bilder 2026-01 (Prototyp)',
      description: 'Kiwix/ZIM – Parser noch nicht aktiv.',
      corpus_url: 'https://dumps.wikimedia.org/kiwix/zim/wikipedia/wikipedia_de_all_nopic_2026-01.zim',
      source_id: 'wikipedia-de-zim-nopic-2026-01',
      language: 'de',
      size_hint: '~13.6 GiB',
      recommended: false,
      import_format: 'zim',
      supported: false,
      codecompass_prerender: false,
    },
  ];

  wikiPresets: any[] = [];
  loadingWikiPresets = false;
  selectedWikiPresetId = '';
  wikiCorpusUrl = '';
  wikiSourceId = '';
  wikiLanguage = 'de';
  wikiStrict = false;
  wikiCodeCompassPrerender = true;
  wikiImportBusy = false;
  wikiImportJobId = '';
  wikiImportJob: any = null;
  wikiJobControlBusy = false;
  allJobs: any[] = [];
  private wikiImportPollTimer: ReturnType<typeof setTimeout> | null = null;

  readonly wikiPhaseLabels: Record<string, string> = {
    queued:                   'In Warteschlange…',
    download_parse_normalize: 'Download & Parse läuft (kann Stunden dauern)…',
    index:                    'CodeCompass-Indexierung läuft…',
    paused_after_import:      'Pausiert (nach Download)',
    paused:                   'Pausiert',
    completed:                'Abgeschlossen ✓',
    failed:                   'Fehlgeschlagen',
    cancelled:                'Abgebrochen',
  };

  constructor() {
    this.applyWikiPresets([]);
    this.loadWikiPresets();
    this.loadProfiles();
    this.loadAllJobs();
  }

  hasActiveJob(): boolean {
    return this.allJobs.some((j) => {
      const s = String(j?.status || '').toLowerCase();
      return s === 'running' || s === 'queued';
    });
  }

  activeBlockingJob(): any | null {
    return this.allJobs.find((j) => {
      const s = String(j?.status || '').toLowerCase();
      return s === 'running' || s === 'queued';
    }) || null;
  }

  loadAllJobs(): void {
    if (!this.hub) return;
    this.hubApi.listWikiImportJobs(this.hub.url).subscribe({
      next: (r) => {
        this.allJobs = Array.isArray(r?.jobs) ? r.jobs : [];
        // auto-resume polling for a running/queued job that we don't know about yet
        const active = this.activeBlockingJob();
        if (active && !this.wikiImportJobId) {
          this.wikiImportJobId = String(active.job_id || '');
          this.wikiImportJob   = active;
          const s = String(active.status || '').toLowerCase();
          if (s === 'running' || s === 'queued') {
            this.wikiImportBusy = true;
            this.startWikiImportPolling(this.wikiImportJobId);
          }
        }
      },
      error: () => {},
    });
  }

  cancelOtherJob(jobId: string): void {
    if (!this.hub || !jobId) return;
    this.hubApi.cancelWikiImportJob(this.hub.url, jobId).subscribe({
      next: () => { this.ns.success('Job abgebrochen'); this.loadAllJobs(); },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Abbrechen fehlgeschlagen')),
    });
  }

  jobPhaseLabel(job: any): string {
    const phase = String(job?.phase || job?.status || '').toLowerCase();
    return this.wikiPhaseLabels[phase] || phase || '…';
  }

  ngOnDestroy(): void {
    this.stopWikiImportPolling();
  }

  // ── profiles ─────────────────────────────────────────────────────────────────
  private loadProfiles(): void {
    if (!this.hub) return;
    this.hubApi.listKnowledgeIndexProfiles(this.hub.url).subscribe({
      next: (p) => {
        const items = Array.isArray(p?.items) ? p.items : [];
        this.knowledgeProfiles = items;
        const def = items.find((i: any) => i?.is_default)?.name || items[0]?.name || 'default';
        if (!this.wikiProfileName || this.wikiProfileName === 'default') this.wikiProfileName = def;
      },
      error: () => {},
    });
  }

  // ── presets ─────────────────────────────────────────────────────────────────
  loadWikiPresets(): void {
    if (!this.hub) return;
    this.loadingWikiPresets = true;
    this.hubApi.listWikiPresets(this.hub.url).pipe(finalize(() => { this.loadingWikiPresets = false; })).subscribe({
      next: (p) => this.applyWikiPresets(Array.isArray(p?.items) ? p.items : []),
      error: () => this.applyWikiPresets([]),
    });
  }

  private applyWikiPresets(items: any[]): void {
    const merged = [...(Array.isArray(items) ? items : []), ...this.fallbackWikiPresets];
    const seen = new Set<string>();
    const deduped: any[] = [];
    for (const item of merged) {
      const id = String(item?.id || '').trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      deduped.push(item);
    }
    this.wikiPresets = deduped.length ? deduped : this.fallbackWikiPresets;
    if (!this.selectedWikiPresetId && this.wikiPresets.length) {
      const rec = this.wikiPresets.find((i: any) => !!i?.recommended) || this.wikiPresets[0];
      this.selectedWikiPresetId = String(rec?.id || '').trim();
      this.onWikiPresetChanged();
    }
  }

  selectedWikiPreset(): any | null {
    if (!this.selectedWikiPresetId) return null;
    return this.wikiPresets.find((i: any) => String(i?.id || '') === this.selectedWikiPresetId) || null;
  }

  onWikiPresetChanged(): void {
    const p = this.selectedWikiPreset();
    if (!p) return;
    this.wikiCorpusUrl = String(p?.corpus_url || '').trim();
    this.wikiSourceId  = String(p?.source_id  || '').trim();
    this.wikiLanguage  = String(p?.language    || 'de').trim() || 'de';
    this.wikiCodeCompassPrerender = Boolean(p?.codecompass_prerender);
  }

  wikiImportSafetyHint(): string {
    const p = this.selectedWikiPreset();
    const s = String(p?.size_hint || '');
    if (!s) return '';
    const text = s.toLowerCase();
    const match = text.match(/([0-9]+(?:\.[0-9]+)?)\s*(gb|gib|mb|mib)/);
    if (!match) return '';
    const bytes = Number(match[1]) * (match[2].startsWith('g') ? 1024 ** 3 : 1024 ** 2);
    if (bytes >= 7 * 1024 ** 3) {
      return 'Sehr großer Dump: nur mit stabilem WLAN, externer Stromversorgung und ausreichend freiem Speicher starten.';
    }
    return '';
  }

  canImportWiki(): boolean {
    const p = this.selectedWikiPreset();
    if (p?.supported === false) return false;
    return !!(this.selectedWikiPresetId || this.wikiCorpusUrl.trim());
  }

  // ── import ──────────────────────────────────────────────────────────────────
  importWiki(): void {
    if (!this.hub || !this.canImportWiki()) return;
    const p = this.selectedWikiPreset();
    const payload: any = {
      profile_name: this.wikiProfileName || 'default',
      language: (this.wikiLanguage || 'de').trim().toLowerCase() || 'de',
      strict: this.wikiStrict,
      codecompass_prerender: this.wikiCodeCompassPrerender,
      async: true,
      source_metadata: { imported_from: 'wikipedia_component' },
    };
    if (p && String(p?.corpus_url || '').trim()) {
      payload.corpus_url = String(p.corpus_url).trim();
      if (String(p?.index_url || '').trim()) payload.index_url = String(p.index_url).trim();
    } else {
      payload.corpus_url = this.wikiCorpusUrl.trim();
    }
    if (this.wikiSourceId.trim()) payload.source_id = this.wikiSourceId.trim();

    this.wikiImportBusy = true;
    this.wikiImportJob  = null;
    this.wikiImportJobId = '';
    this.stopWikiImportPolling();

    this.hubApi.importWikiFromUrl(this.hub.url, payload)
      .pipe(finalize(() => { if (!this.wikiImportJobId) this.wikiImportBusy = false; }))
      .subscribe({
        next: (r) => {
          const jobId = String(r?.job?.job_id || r?.job?.id || '').trim();
          if (jobId) {
            this.wikiImportJobId = jobId;
            this.wikiImportJob   = r?.job || null;
            this.wikiImportBusy  = true;
            this.ns.success(`Wikipedia-Import gestartet (Job ${jobId})`);
            this.startWikiImportPolling(jobId);
          } else {
            this.wikiImportBusy = false;
            this.ns.success('Wikipedia-Import abgeschlossen');
          }
        },
        error: (e) => {
          this.wikiImportBusy = false;
          this.ns.error(this.ns.fromApiError(e, 'Wikipedia-Import fehlgeschlagen'));
        },
      });
  }

  private startWikiImportPolling(jobId: string): void {
    if (!this.hub) return;
    this.stopWikiImportPolling();
    const poll = () => {
      if (!this.hub) return;
      this.hubApi.getWikiImportJob(this.hub.url, jobId).subscribe({
        next: (r) => {
          this.wikiImportJob = r?.job || null;
          const status = String(this.wikiImportJob?.status || '').trim().toLowerCase();
          if (status === 'completed') {
            this.wikiImportBusy = false;
            this.stopWikiImportPolling();
            this.ns.success('Wikipedia-Import abgeschlossen — RAG-Quelle aktiv');
          } else if (status === 'failed' || status === 'cancelled') {
            this.wikiImportBusy = false;
            this.stopWikiImportPolling();
            const err = String(this.wikiImportJob?.error || '').trim();
            this.ns.error(err ? `Import fehlgeschlagen: ${err}` : 'Import fehlgeschlagen');
          } else {
            this.wikiImportPollTimer = setTimeout(poll, 2000);
          }
        },
        error: () => { this.wikiImportPollTimer = setTimeout(poll, 4000); },
      });
    };
    this.wikiImportPollTimer = setTimeout(poll, 800);
  }

  private stopWikiImportPolling(): void {
    if (this.wikiImportPollTimer) { clearTimeout(this.wikiImportPollTimer); this.wikiImportPollTimer = null; }
  }

  // ── job controls ─────────────────────────────────────────────────────────────
  wikiJobStatus(): string { return String(this.wikiImportJob?.status || '').trim().toLowerCase(); }
  wikiPhaseLabel(): string {
    const phase = String(this.wikiImportJob?.phase || this.wikiJobStatus() || '').toLowerCase();
    return this.wikiPhaseLabels[phase] || phase || '…';
  }
  wikiProgressPercent(): number { return Number(this.wikiImportJob?.progress_percent ?? (this.wikiImportBusy ? 5 : 0)); }
  wikiImportStats(): { records: number; issues: number; source_id: string } | null {
    const r = this.wikiImportJob?.import_report;
    if (!r) return null;
    return {
      records:   Number(r?.stats?.normalized_records || r?.stats?.records_total || 0),
      issues:    Number(r?.issues?.length || 0),
      source_id: String(r?.source_id || ''),
    };
  }

  canPauseWikiJob():  boolean { return !!this.wikiImportJobId && this.wikiJobStatus() === 'running'  && !this.wikiJobControlBusy; }
  canResumeWikiJob(): boolean { return !!this.wikiImportJobId && this.wikiJobStatus() === 'paused'   && !this.wikiJobControlBusy; }
  canCancelWikiJob(): boolean {
    const s = this.wikiJobStatus();
    return !!this.wikiImportJobId && (s === 'running' || s === 'paused') && !this.wikiJobControlBusy;
  }
  canRetryWikiImport(): boolean {
    const s = this.wikiJobStatus();
    return !this.wikiImportBusy && (s === 'failed' || s === 'cancelled');
  }

  pauseWikiJob(): void {
    if (!this.hub || !this.wikiImportJobId) return;
    this.wikiJobControlBusy = true;
    this.hubApi.pauseWikiImportJob(this.hub.url, this.wikiImportJobId).subscribe({
      next: (r) => { this.wikiImportJob = r?.job || this.wikiImportJob; this.wikiJobControlBusy = false; },
      error: (e) => { this.ns.error(this.ns.fromApiError(e, 'Pause fehlgeschlagen')); this.wikiJobControlBusy = false; },
    });
  }

  resumeWikiJob(): void {
    if (!this.hub || !this.wikiImportJobId) return;
    this.wikiJobControlBusy = true;
    this.hubApi.resumeWikiImportJob(this.hub.url, this.wikiImportJobId).subscribe({
      next: (r) => {
        this.wikiImportJob = r?.job || this.wikiImportJob;
        this.wikiJobControlBusy = false;
        this.wikiImportBusy = true;
        this.startWikiImportPolling(this.wikiImportJobId);
      },
      error: (e) => { this.ns.error(this.ns.fromApiError(e, 'Fortsetzen fehlgeschlagen')); this.wikiJobControlBusy = false; },
    });
  }

  cancelWikiJob(): void {
    if (!this.hub || !this.wikiImportJobId) return;
    this.wikiJobControlBusy = true;
    this.hubApi.cancelWikiImportJob(this.hub.url, this.wikiImportJobId).subscribe({
      next: (r) => {
        this.wikiImportJob = r?.job || this.wikiImportJob;
        this.wikiJobControlBusy = false;
        this.wikiImportBusy = false;
        this.stopWikiImportPolling();
      },
      error: (e) => { this.ns.error(this.ns.fromApiError(e, 'Abbrechen fehlgeschlagen')); this.wikiJobControlBusy = false; },
    });
  }

  retryWikiImport(): void {
    this.wikiImportJob   = null;
    this.wikiImportJobId = '';
    this.importWiki();
  }
}
