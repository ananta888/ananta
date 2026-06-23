import { Component, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { AdminFacade } from '../features/admin/admin.facade';
import { AgentApiService } from '../services/agent-api.service';
import { decisionExplanation, userFacingTerm } from '../models/user-facing-language';
import { SummaryMetric, SummaryPanelComponent, TableShellComponent } from '../shared/ui/display';

@Component({
  standalone: true,
  selector: 'app-artifacts',
  imports: [CommonModule, FormsModule, UiSkeletonComponent, SummaryPanelComponent, TableShellComponent],
  styleUrls: ['./artifacts.component.css'],
  templateUrl: './artifacts.component.html',
})
export class ArtifactsComponent implements OnDestroy {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(AdminFacade);
  private ns = inject(NotificationService);
  private agentApi = inject(AgentApiService);

  hub = this.dir.list().find((a) => a.role === 'hub');
  artifacts: any[] = [];
  selectedArtifactId: string | null = null;
  selectedArtifact: any = null;
  collectionName = '';
  newCollectionName = '';
  newCollectionDescription = '';
  selectedFile: File | null = null;
  loadingList = false;
  loadingDetail = false;
  loadingCollections = false;
  uploadBusy = false;
  extractBusy = false;
  indexBusy = false;
  previewBusy = false;
  collectionBusy = false;
  collectionIndexBusy = false;
  searchBusy = false;
  knowledgeCollections: any[] = [];
  knowledgeProfiles: any[] = [];
  selectedCollectionId: string | null = null;
  selectedCollectionDetail: any = null;
  artifactRagStatus: any = null;
  artifactRagPreview: any = null;
  knowledgeSearchQuery = '';
  knowledgeSearchResults: any[] = [];
  selectedArtifactProfileName = 'default';
  selectedCollectionProfileName = 'default';
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
      description: 'Fallback ohne Multistream-Index; meist weniger praktisch fuer grosse lokale Verarbeitung.',
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
      description: 'Kleiner Kiwix/ZIM-Prototyp-Dump. Sichtbar fuer Download-Planung; Import benoetigt noch ZIM-Parser.',
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
      description: 'Groesserer Kiwix/ZIM-Dump ohne Bilder. Sichtbar fuer spaetere ZIM-Unterstuetzung.',
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
  wikiLanguage = 'en';
  wikiStrict = false;
  wikiCodeCompassPrerender = true;
  wikiImportBusy = false;
  wikiImportResult: any = null;
  wikiImportJobId = '';
  wikiImportJob: any = null;
  wikiJobControlBusy = false;
  private wikiImportPollTimer: ReturnType<typeof setTimeout> | null = null;

  readonly wikiPhaseLabels: Record<string, string> = {
    queued:                   'In Warteschlange…',
    download_parse_normalize: 'Download & Parse läuft (kann Stunden dauern bei großem Dump)…',
    index:                    'CodeCompass-Indexierung läuft…',
    paused_after_import:      'Pausiert (nach Download)',
    paused:                   'Pausiert',
    completed:                'Abgeschlossen',
    failed:                   'Fehlgeschlagen',
    cancelled:                'Abgebrochen',
  };

  wikiPhaseLabel(): string {
    const phase = String(this.wikiImportJob?.phase || this.wikiJobStatus() || '').toLowerCase();
    return this.wikiPhaseLabels[phase] || phase || '…';
  }

  wikiProgressPercent(): number {
    return Number(this.wikiImportJob?.progress_percent ?? (this.wikiImportBusy ? 5 : 0));
  }

  wikiImportStats(): { records: number; issues: number; source_id: string } | null {
    const r = this.wikiImportJob?.import_report;
    if (!r) return null;
    return {
      records: Number(r?.stats?.normalized_records || r?.stats?.records_total || 0),
      issues:  Number(r?.issues?.length || 0),
      source_id: String(r?.source_id || ''),
    };
  }
  artifactFlowReadModel: any = null;
  loadingArtifactFlow = false;
  selectedWorkspaceRunKey = '';
  workspaceTrackedOnly = true;
  workspaceLoading = false;
  workspaceLoadError = '';
  workspaceFilePayload: any = null;
  workspaceTreeLineItems: any[] = [];
  term = userFacingTerm;
  decisionExplanation = decisionExplanation;

  constructor() {
    this.applyWikiPresets([]);
    this.refresh();
    this.loadCollections();
    this.loadProfiles();
    this.loadWikiPresets();
    this.loadArtifactFlow();
  }

  ngOnDestroy(): void {
    this.stopWikiImportPolling();
  }

  selectedArtifactSummaryMetrics(): SummaryMetric[] {
    return [
      { label: 'Versionen', value: this.selectedArtifact?.versions?.length || 0 },
      { label: 'Extrahierte Dokumente', value: this.selectedArtifact?.extracted_documents?.length || 0 },
      { label: 'Wissenslinks', value: this.selectedArtifact?.knowledge_links?.length || 0 },
      {
        label: 'RAG-Status',
        value: this.selectedArtifact?.knowledge_index?.status || this.artifactRagStatus?.knowledge_index?.status || 'nicht indexiert',
      },
      {
        label: 'Index-Profil',
        value: this.selectedArtifact?.knowledge_index?.profile_name || this.artifactRagStatus?.knowledge_index?.profile_name || 'default',
      },
    ];
  }

  refresh() {
    if (!this.hub) return;
    this.loadingList = true;
    this.hubApi.listArtifacts(this.hub.url).pipe(
      finalize(() => {
        this.loadingList = false;
      }),
    ).subscribe({
      next: (items) => {
        this.artifacts = Array.isArray(items) ? items : [];
        if (!this.selectedArtifactId && this.artifacts.length) {
          this.selectArtifact(this.artifacts[0].id);
          return;
        }
        if (this.selectedArtifactId) {
          const stillExists = this.artifacts.some((item) => item.id === this.selectedArtifactId);
          if (stillExists) {
            this.selectArtifact(this.selectedArtifactId);
          } else {
            this.selectedArtifactId = null;
            this.selectedArtifact = null;
          }
        }
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Artefakte konnten nicht geladen werden')),
    });
    this.loadArtifactFlow();
  }

  loadArtifactFlow() {
    if (!this.hub) return;
    this.loadingArtifactFlow = true;
    this.hubApi.getTaskOrchestrationReadModel(this.hub.url).pipe(
      finalize(() => {
        this.loadingArtifactFlow = false;
      }),
    ).subscribe({
      next: (payload) => {
        this.artifactFlowReadModel = payload?.artifact_flow || null;
        this.ensureWorkspaceSelection();
      },
      error: (error) => {
        this.artifactFlowReadModel = null;
        this.selectedWorkspaceRunKey = '';
        this.workspaceFilePayload = null;
        this.workspaceTreeLineItems = [];
        this.ns.error(this.ns.fromApiError(error, 'Artefakt-Fluss konnte nicht geladen werden'));
      },
    });
  }

  loadCollections() {
    if (!this.hub) return;
    this.loadingCollections = true;
    this.hubApi.listKnowledgeCollections(this.hub.url).pipe(
      finalize(() => {
        this.loadingCollections = false;
      }),
    ).subscribe({
      next: (items) => {
        this.knowledgeCollections = Array.isArray(items) ? items : [];
        if (!this.selectedCollectionId && this.knowledgeCollections.length) {
          this.selectCollection(this.knowledgeCollections[0].id);
        }
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Collections konnten nicht geladen werden')),
    });
  }

  loadProfiles() {
    if (!this.hub) return;
    this.hubApi.listKnowledgeIndexProfiles(this.hub.url).subscribe({
      next: (payload) => {
        const items = Array.isArray(payload?.items) ? payload.items : [];
        this.knowledgeProfiles = items;
        const defaultProfile = items.find((item: any) => item?.is_default)?.name || items[0]?.name || 'default';
        if (!this.selectedArtifactProfileName) this.selectedArtifactProfileName = defaultProfile;
        if (!this.selectedCollectionProfileName) this.selectedCollectionProfileName = defaultProfile;
      },
      error: () => {
        this.knowledgeProfiles = [];
      },
    });
  }

  loadWikiPresets() {
    if (!this.hub) return;
    this.loadingWikiPresets = true;
    this.hubApi.listWikiPresets(this.hub.url).pipe(
      finalize(() => {
        this.loadingWikiPresets = false;
      }),
    ).subscribe({
      next: (payload) => {
        const items = Array.isArray(payload?.items) ? payload.items : [];
        this.applyWikiPresets(items);
      },
      error: () => {
        this.applyWikiPresets([]);
      },
    });
  }

  private applyWikiPresets(items: any[]) {
    const remote = Array.isArray(items) ? items : [];
    const merged = [...remote, ...this.fallbackWikiPresets];
    const deduped: any[] = [];
    const seen = new Set<string>();
    for (const item of merged) {
      const id = String(item?.id || '').trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      deduped.push(item);
    }
    const effective = deduped.length ? deduped : this.fallbackWikiPresets;
    this.wikiPresets = effective;
    if (!this.selectedWikiPresetId && effective.length) {
      const recommended = effective.find((item: any) => !!item?.recommended) || effective[0];
      this.selectedWikiPresetId = String(recommended?.id || '').trim();
      this.onWikiPresetChanged();
    }
  }

  selectedWikiPreset(): any | null {
    if (!this.selectedWikiPresetId) return null;
    return this.wikiPresets.find((item: any) => String(item?.id || '') === this.selectedWikiPresetId) || null;
  }

  onWikiPresetChanged() {
    const preset = this.selectedWikiPreset();
    if (!preset) return;
    this.wikiCorpusUrl = String(preset?.corpus_url || '').trim();
    this.wikiSourceId = String(preset?.source_id || '').trim();
    this.wikiLanguage = String(preset?.language || 'en').trim() || 'en';
    this.wikiCodeCompassPrerender = Boolean(preset?.codecompass_prerender);
  }

  private parseWikiSizeHintBytes(value: string): number {
    const text = String(value || '').toLowerCase();
    const match = text.match(/([0-9]+(?:\.[0-9]+)?)\s*(gb|gib|mb|mib)/);
    if (!match) return 0;
    const size = Number(match[1] || 0);
    const unit = String(match[2] || '');
    if (!Number.isFinite(size) || size <= 0) return 0;
    if (unit === 'gb' || unit === 'gib') return Math.round(size * 1024 * 1024 * 1024);
    return Math.round(size * 1024 * 1024);
  }

  wikiImportSafetyHint(): string {
    const preset = this.selectedWikiPreset();
    const sizeHint = String(preset?.size_hint || '');
    const approxBytes = this.parseWikiSizeHintBytes(sizeHint);
    if (!sizeHint) return 'Mobile Signale (WLAN, Charging, verfuegbarer Speicher) sind aktuell unbekannt.';
    if (approxBytes >= 7 * 1024 * 1024 * 1024) {
      return 'Sehr grosser Dump: fuer Android nur mit stabilem WLAN, externer Stromversorgung und ausreichend freiem Speicher starten. Ohne Signale bleibt der Status unknown.';
    }
    return 'Import-Hinweis: Mobile Signale koennen unknown sein; bitte WLAN/Charging/Speicher vor Start manuell pruefen.';
  }

  canImportWiki(): boolean {
    const preset = this.selectedWikiPreset();
    if (preset?.supported === false) return false;
    if (this.selectedWikiPresetId) return true;
    return !!this.wikiCorpusUrl.trim();
  }

  importWiki() {
    if (!this.hub || !this.canImportWiki()) return;
    const payload: any = {
      profile_name: this.selectedCollectionProfileName || 'default',
      language: (this.wikiLanguage || 'en').trim().toLowerCase() || 'en',
      strict: this.wikiStrict,
      codecompass_prerender: this.wikiCodeCompassPrerender,
      async: true,
      source_metadata: {
        imported_from: 'artifacts_component',
      },
    };
    const preset = this.selectedWikiPreset();
    if (preset && String(preset?.corpus_url || '').trim()) {
      payload.corpus_url = String(preset.corpus_url).trim();
      if (String(preset?.index_url || '').trim()) {
        payload.index_url = String(preset.index_url).trim();
      }
    } else {
      payload.corpus_url = this.wikiCorpusUrl.trim();
    }
    if (this.wikiSourceId.trim()) {
      payload.source_id = this.wikiSourceId.trim();
    }
    this.wikiImportBusy = true;
    this.wikiImportResult = null;
    this.wikiImportJob = null;
    this.wikiImportJobId = '';
    this.stopWikiImportPolling();
    this.hubApi.importWikiFromUrl(this.hub.url, payload).pipe(
      finalize(() => {
        if (!this.wikiImportJobId) {
          this.wikiImportBusy = false;
        }
      }),
    ).subscribe({
      next: (response) => {
        this.wikiImportResult = response?.import_report || null;
        const jobId = String(response?.job?.job_id || response?.job?.id || '').trim();
        if (jobId) {
          this.wikiImportJobId = jobId;
          this.wikiImportJob = response?.job || null;
          this.wikiImportBusy = true;
          this.ns.success(`Wikipedia-Import gestartet (Job ${jobId})`);
          this.startWikiImportPolling(jobId);
          return;
        }
        this.wikiImportBusy = false;
        this.ns.success('Wikipedia-Import abgeschlossen');
        this.loadCollections();
      },
      error: (error) => {
        this.wikiImportBusy = false;
        this.stopWikiImportPolling();
        this.ns.error(this.ns.fromApiError(error, 'Wikipedia-Import fehlgeschlagen'));
      },
    });
  }

  wikiJobStatus(): string {
    return String(this.wikiImportJob?.status || '').trim().toLowerCase();
  }

  canPauseWikiJob(): boolean {
    return !!this.wikiImportJobId && this.wikiJobStatus() === 'running' && !this.wikiJobControlBusy;
  }

  canResumeWikiJob(): boolean {
    return !!this.wikiImportJobId && this.wikiJobStatus() === 'paused' && !this.wikiJobControlBusy;
  }

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
    this.wikiImportJob = null;
    this.wikiImportJobId = '';
    this.wikiImportResult = null;
    this.importWiki();
  }

  private startWikiImportPolling(jobId: string): void {
    if (!this.hub) return;
    this.stopWikiImportPolling();
    const poll = () => {
      if (!this.hub) return;
      this.hubApi.getWikiImportJob(this.hub.url, jobId).subscribe({
        next: (response) => {
          const job = response?.job || null;
          this.wikiImportJob = job;
          const status = String(job?.status || '').trim().toLowerCase();
          if (status === 'completed') {
            this.wikiImportBusy = false;
            this.stopWikiImportPolling();
            this.ns.success('Wikipedia-Import abgeschlossen');
            this.loadCollections();
            return;
          }
          if (status === 'failed' || status === 'cancelled') {
            this.wikiImportBusy = false;
            this.stopWikiImportPolling();
            const errorHint = String(job?.error || '').trim();
            this.ns.error(errorHint ? `Wikipedia-Import fehlgeschlagen: ${errorHint}` : 'Wikipedia-Import fehlgeschlagen');
            return;
          }
          this.wikiImportPollTimer = setTimeout(poll, 2000);
        },
        error: () => {
          this.wikiImportPollTimer = setTimeout(poll, 4000);
        },
      });
    };
    this.wikiImportPollTimer = setTimeout(poll, 800);
  }

  private stopWikiImportPolling(): void {
    if (this.wikiImportPollTimer) {
      clearTimeout(this.wikiImportPollTimer);
      this.wikiImportPollTimer = null;
    }
  }

  onFileSelected(event: Event) {
    const input = event.target as HTMLInputElement | null;
    this.selectedFile = input?.files?.[0] || null;
  }

  createCollection() {
    if (!this.hub || !this.newCollectionName.trim()) return;
    this.collectionBusy = true;
    this.hubApi.createKnowledgeCollection(this.hub.url, {
      name: this.newCollectionName.trim(),
      description: this.newCollectionDescription.trim() || undefined,
    }).pipe(
      finalize(() => {
        this.collectionBusy = false;
      }),
    ).subscribe({
      next: (collection) => {
        this.ns.success('Collection angelegt');
        this.newCollectionName = '';
        this.newCollectionDescription = '';
        this.loadCollections();
        if (collection?.id) {
          this.selectCollection(collection.id);
        }
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Collection konnte nicht angelegt werden')),
    });
  }

  upload() {
    if (!this.hub || !this.selectedFile) return;
    this.uploadBusy = true;
    this.hubApi.uploadArtifact(this.hub.url, this.selectedFile, this.collectionName).pipe(
      finalize(() => {
        this.uploadBusy = false;
      }),
    ).subscribe({
      next: (result) => {
        const artifactId = result?.artifact?.id || null;
        this.ns.success('Artefakt hochgeladen');
        this.selectedFile = null;
        this.collectionName = '';
        this.refresh();
        if (artifactId) {
          this.selectedArtifactId = artifactId;
          this.selectArtifact(artifactId);
        }
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Upload fehlgeschlagen')),
    });
  }

  selectArtifact(artifactId: string) {
    if (!this.hub || !artifactId) return;
    this.selectedArtifactId = artifactId;
    this.loadingDetail = true;
    this.hubApi.getArtifact(this.hub.url, artifactId).pipe(
      finalize(() => {
        this.loadingDetail = false;
      }),
    ).subscribe({
      next: (payload) => {
        this.selectedArtifact = payload;
        this.artifactRagStatus = null;
        this.artifactRagPreview = null;
        this.loadSelectedRagDetails();
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Artifact-Details konnten nicht geladen werden')),
    });
  }

  extractSelected() {
    if (!this.hub || !this.selectedArtifactId) return;
    this.extractBusy = true;
    this.hubApi.extractArtifact(this.hub.url, this.selectedArtifactId).pipe(
      finalize(() => {
        this.extractBusy = false;
      }),
    ).subscribe({
      next: () => {
        this.ns.success('Extraktion gestartet');
        this.selectArtifact(this.selectedArtifactId!);
        this.refresh();
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Extraktion fehlgeschlagen')),
    });
  }

  indexSelected() {
    if (!this.hub || !this.selectedArtifactId) return;
    this.indexBusy = true;
    this.hubApi.indexArtifact(this.hub.url, this.selectedArtifactId, {
      profile_name: this.selectedArtifactProfileName || 'default',
    }).pipe(
      finalize(() => {
        this.indexBusy = false;
      }),
    ).subscribe({
      next: () => {
        this.ns.success('RAG-Index erstellt');
        this.selectArtifact(this.selectedArtifactId!);
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'RAG-Index fehlgeschlagen')),
    });
  }

  loadSelectedRagDetails() {
    if (!this.hub || !this.selectedArtifactId) return;
    this.previewBusy = true;
    this.hubApi.getArtifactRagStatus(this.hub.url, this.selectedArtifactId).subscribe({
      next: (payload) => {
        this.artifactRagStatus = payload;
      },
      error: () => {
        this.artifactRagStatus = null;
      },
    });
    this.hubApi.getArtifactRagPreview(this.hub.url, this.selectedArtifactId, 5).pipe(
      finalize(() => {
        this.previewBusy = false;
      }),
    ).subscribe({
      next: (payload) => {
        this.artifactRagPreview = payload;
      },
      error: () => {
        this.artifactRagPreview = null;
      },
    });
  }

  selectCollection(collectionId: string) {
    if (!this.hub || !collectionId) return;
    this.selectedCollectionId = collectionId;
    this.hubApi.getKnowledgeCollection(this.hub.url, collectionId).subscribe({
      next: (payload) => {
        this.selectedCollectionDetail = payload;
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Collection-Details konnten nicht geladen werden')),
    });
  }

  indexSelectedCollection() {
    if (!this.hub || !this.selectedCollectionId) return;
    this.collectionIndexBusy = true;
    this.hubApi.indexKnowledgeCollection(this.hub.url, this.selectedCollectionId, {
      profile_name: this.selectedCollectionProfileName || 'default',
    }).pipe(
      finalize(() => {
        this.collectionIndexBusy = false;
      }),
    ).subscribe({
      next: () => {
        this.ns.success('Collection indexiert');
        this.selectCollection(this.selectedCollectionId!);
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Collection-Index fehlgeschlagen')),
    });
  }

  searchSelectedCollection() {
    if (!this.hub || !this.selectedCollectionId || !this.knowledgeSearchQuery.trim()) return;
    this.searchBusy = true;
    this.hubApi.searchKnowledgeCollection(this.hub.url, this.selectedCollectionId, {
      query: this.knowledgeSearchQuery.trim(),
      top_k: 5,
    }).pipe(
      finalize(() => {
        this.searchBusy = false;
      }),
    ).subscribe({
      next: (payload) => {
        this.knowledgeSearchResults = Array.isArray(payload?.chunks) ? payload.chunks : [];
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Collection-Suche fehlgeschlagen')),
    });
  }

  knowledgeCollectionNames(payload: any): string[] {
    const links = Array.isArray(payload?.knowledge_links) ? payload.knowledge_links : [];
    const names = links
      .map((link: any) => String(link?.link_metadata?.collection_name || link?.collection_id || '').trim())
      .filter((value: string) => !!value);
    return Array.from(new Set(names));
  }

  activeCollectionProfile(): any {
    return this.knowledgeProfiles.find((profile: any) => profile?.name === this.selectedCollectionProfileName) || null;
  }

  artifactFlowItems(): any[] {
    return Array.isArray(this.artifactFlowReadModel?.items) ? this.artifactFlowReadModel.items : [];
  }

  artifactFlowWorkerGroups(): any[] {
    return Array.isArray(this.artifactFlowReadModel?.groups?.by_worker) ? this.artifactFlowReadModel.groups.by_worker : [];
  }

  artifactFlowAssignmentGroups(): any[] {
    return Array.isArray(this.artifactFlowReadModel?.groups?.by_assignment) ? this.artifactFlowReadModel.groups.by_assignment : [];
  }

  itemArtifacts(item: any): any[] {
    const artifacts = [
      ...(Array.isArray(item?.sent_artifacts) ? item.sent_artifacts : []),
      ...(Array.isArray(item?.returned_artifacts) ? item.returned_artifacts : []),
      ...((Array.isArray(item?.worker_jobs) ? item.worker_jobs : []).flatMap((job: any) => [
        ...(Array.isArray(job?.sent_artifacts) ? job.sent_artifacts : []),
        ...(Array.isArray(job?.returned_artifacts) ? job.returned_artifacts : []),
      ])),
    ];
    return this.uniqueArtifacts(artifacts);
  }

  groupArtifacts(group: any): any[] {
    return this.uniqueArtifacts(Array.isArray(group?.artifacts) ? group.artifacts : []);
  }

  itemWorkspaceFiles(item: any): any[] {
    const workerJobs = Array.isArray(item?.worker_jobs) ? item.worker_jobs : [];
    const files = workerJobs.flatMap((job: any) => this.workspaceFilesFromRefs(job?.returned_refs, {
      worker_job_id: job?.worker_job_id,
      worker_url: job?.worker_url,
      worker_name: job?.worker_name,
    }));
    return this.uniqueWorkspaceFiles(files);
  }

  workerWorkspaceFiles(group: any): any[] {
    const workerUrl = String(group?.worker_url || '').trim();
    if (!workerUrl) return [];
    const files = this.artifactFlowItems().flatMap((item: any) => {
      const workerJobs = Array.isArray(item?.worker_jobs) ? item.worker_jobs : [];
      return workerJobs
        .filter((job: any) => String(job?.worker_url || '').trim() === workerUrl)
        .flatMap((job: any) => this.workspaceFilesFromRefs(job?.returned_refs, {
          worker_job_id: job?.worker_job_id,
          worker_url: job?.worker_url,
          worker_name: job?.worker_name,
        }));
    });
    return this.uniqueWorkspaceFiles(files);
  }

  artifactCount(item: any): number {
    return this.itemArtifacts(item).length;
  }

  assignmentLabel(group: any): string {
    return String(group?.template_name || group?.agent_name || group?.assignment_key || 'Unbekannte Zuordnung').trim();
  }

  workspaceCandidates(): any[] {
    const candidates: any[] = [];
    const seen = new Set<string>();
    for (const item of this.artifactFlowItems()) {
      const workerJobs = Array.isArray(item?.worker_jobs) ? item.worker_jobs : [];
      for (const job of workerJobs) {
        const workerUrl = String(job?.worker_url || '').trim();
        const subtaskId = String(job?.subtask_id || '').trim();
        if (!workerUrl || !subtaskId) continue;
        const key = `${workerUrl}::${subtaskId}`;
        if (seen.has(key)) continue;
        seen.add(key);
        candidates.push({
          key,
          worker_url: workerUrl,
          worker_name: String(job?.worker_name || '').trim() || workerUrl,
          task_id: subtaskId,
          worker_job_id: String(job?.worker_job_id || '').trim() || undefined,
          task_title: String(item?.title || item?.task_title || item?.task_id || '').trim(),
          updated_at: Number(job?.updated_at || item?.updated_at || 0),
        });
      }
    }
    candidates.sort((a, b) => (Number(b.updated_at || 0) - Number(a.updated_at || 0)));
    return candidates;
  }

  workspaceRunLabel(candidate: any): string {
    const worker = String(candidate?.worker_name || candidate?.worker_url || 'worker').trim();
    const taskId = String(candidate?.task_id || '').trim();
    const title = String(candidate?.task_title || '').trim();
    if (!title) return `${worker} · ${taskId}`;
    return `${worker} · ${taskId} · ${title}`;
  }

  workspaceSelectionChanged() {
    this.workspaceFilePayload = null;
    this.workspaceTreeLineItems = [];
    this.workspaceLoadError = '';
  }

  loadSelectedWorkspaceFiles() {
    const candidate = this.workspaceCandidates().find((item: any) => item.key === this.selectedWorkspaceRunKey);
    if (!candidate) return;
    this.workspaceLoading = true;
    this.workspaceLoadError = '';
    this.agentApi.taskWorkspaceFiles(
      candidate.worker_url,
      candidate.task_id,
      undefined,
      { trackedOnly: this.workspaceTrackedOnly, maxEntries: 4000 },
    ).pipe(
      finalize(() => {
        this.workspaceLoading = false;
      }),
    ).subscribe({
      next: (payload) => {
        this.workspaceFilePayload = payload;
        const files = Array.isArray(payload?.workspace?.files) ? payload.workspace.files : [];
        this.workspaceTreeLineItems = this.buildWorkspaceTreeLines(files);
      },
      error: (error) => {
        this.workspaceFilePayload = null;
        this.workspaceTreeLineItems = [];
        this.workspaceLoadError = this.ns.fromApiError(error, 'Workspace-Dateien konnten nicht geladen werden');
      },
    });
  }

  workspaceInspectorMeta(): any {
    const workspace = this.workspaceFilePayload?.workspace;
    return workspace && typeof workspace === 'object' ? workspace : null;
  }

  workspaceTreeLines(): any[] {
    return this.workspaceTreeLineItems;
  }

  selectArtifactBySummary(artifact: any) {
    const artifactId = String(artifact?.artifact_id || '').trim();
    if (!artifactId) return;
    this.selectArtifact(artifactId);
  }

  private uniqueArtifacts(artifacts: any[]): any[] {
    const seen = new Set<string>();
    return artifacts.filter((artifact) => {
      const artifactId = String(artifact?.artifact_id || '').trim();
      if (!artifactId || seen.has(artifactId)) return false;
      seen.add(artifactId);
      return true;
    });
  }

  private workspaceFilesFromRefs(refs: any, fallback: { worker_job_id?: string; worker_url?: string; worker_name?: string }): any[] {
    const rows = Array.isArray(refs) ? refs : [];
    const files = rows
      .filter((ref: any) => ref && typeof ref === 'object')
      .map((ref: any) => {
        const workspaceRelativePath = String(ref.workspace_relative_path || '').trim();
        if (!workspaceRelativePath) return null;
        return {
          kind: String(ref.kind || '').trim() || 'workspace_file',
          workspace_relative_path: workspaceRelativePath,
          artifact_id: String(ref.artifact_id || '').trim() || undefined,
          filename: String(ref.filename || '').trim() || undefined,
          worker_job_id: String(ref.worker_job_id || fallback.worker_job_id || '').trim() || undefined,
          worker_url: String(ref.worker_url || fallback.worker_url || '').trim() || undefined,
          worker_name: String(ref.worker_name || fallback.worker_name || '').trim() || undefined,
        };
      })
      .filter((entry: any) => !!entry);
    return files as any[];
  }

  private uniqueWorkspaceFiles(files: any[]): any[] {
    const seen = new Set<string>();
    return files.filter((file) => {
      const key = [
        String(file?.worker_url || '').trim(),
        String(file?.worker_job_id || '').trim(),
        String(file?.workspace_relative_path || '').trim(),
        String(file?.artifact_id || '').trim(),
      ].join('|');
      if (!key.replace(/\|/g, '').trim()) return false;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  private ensureWorkspaceSelection() {
    const candidates = this.workspaceCandidates();
    const hasCurrent = candidates.some((item: any) => item.key === this.selectedWorkspaceRunKey);
    if (hasCurrent) return;
    this.selectedWorkspaceRunKey = candidates[0]?.key || '';
    this.workspaceFilePayload = null;
    this.workspaceTreeLineItems = [];
    this.workspaceLoadError = '';
  }

  private buildWorkspaceTreeLines(files: any[]): any[] {
    const rows = Array.isArray(files) ? files : [];
    const normalized = rows
      .map((item: any) => ({
        relative_path: String(item?.relative_path || '').trim().replace(/\\/g, '/'),
        size_bytes: Number(item?.size_bytes || 0),
      }))
      .filter((item: any) => !!item.relative_path)
      .sort((a: any, b: any) => a.relative_path.localeCompare(b.relative_path));

    const lines: any[] = [];
    const seenDirs = new Set<string>();
    for (const file of normalized) {
      const parts = file.relative_path.split('/').filter((part: string) => !!part);
      if (!parts.length) continue;
      for (let index = 0; index < parts.length - 1; index += 1) {
        const dirPath = parts.slice(0, index + 1).join('/');
        if (seenDirs.has(dirPath)) continue;
        seenDirs.add(dirPath);
        lines.push({
          type: 'dir',
          path: dirPath,
          name: parts[index],
          depth: index,
        });
      }
      lines.push({
        type: 'file',
        path: file.relative_path,
        name: parts[parts.length - 1],
        depth: Math.max(0, parts.length - 1),
        size_bytes: Number.isFinite(file.size_bytes) ? file.size_bytes : 0,
      });
    }
    return lines;
  }
}
