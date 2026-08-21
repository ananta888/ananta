import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { forkJoin } from 'rxjs';
import { KnowledgeHygieneApiService } from './knowledge-hygiene-api.service';
import {
  CuratedWikiPage,
  KnowledgeConflict,
  KnowledgeConflictDetail,
  KnowledgeCoverage,
  KnowledgeHealthSnapshot,
  KnowledgeCorrectionDetail,
} from './knowledge-hygiene.models';

type ViewTab = 'health' | 'conflicts' | 'wiki';

@Component({
  selector: 'app-knowledge-hygiene-page',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './knowledge-hygiene-page.component.html',
  styleUrl: './knowledge-hygiene-page.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class KnowledgeHygienePageComponent {
  private readonly api = inject(KnowledgeHygieneApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);

  readonly projectId = signal('');
  readonly activeTab = signal<ViewTab>('health');
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly health = signal<KnowledgeHealthSnapshot | null>(null);
  readonly conflicts = signal<KnowledgeConflict[]>([]);
  readonly wikiPages = signal<CuratedWikiPage[]>([]);
  readonly selectedConflict = signal<KnowledgeConflictDetail | null>(null);
  readonly selectedPage = signal<CuratedWikiPage | null>(null);
  readonly decision = signal<'keep_left' | 'keep_right' | 'keep_both' | 'request_correction' | 'dismiss_not_conflict'>('keep_both');
  readonly rationale = signal('');
  readonly qualifiers = signal('');
  readonly requestWriteback = signal(false);
  readonly actionPending = signal(false);
  readonly correctionId = signal('');
  readonly correctionDetail = signal<KnowledgeCorrectionDetail | null>(null);
  readonly coverageMessage = computed(() => {
    const coverage = this.health()?.coverage ?? 'unknown';
    if (coverage === 'complete') return 'Vollstaendige Auswertung: Nullwerte sind belastbar.';
    if (coverage === 'partial') return 'Teilabdeckung: Kennzahlen zeigen nur beobachtete Datensaetze.';
    return 'Abdeckung unbekannt: Fehlende Treffer sind kein Negativnachweis.';
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(params => {
      const projectId = params.get('projectId') ?? '';
      this.projectId.set(projectId);
      this.load(projectId);
    });
  }

  selectTab(tab: ViewTab): void {
    this.activeTab.set(tab);
  }

  selectConflict(conflict: KnowledgeConflict): void {
    this.error.set(null);
    this.api.conflict(this.projectId(), conflict.conflict_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: detail => this.selectedConflict.set(detail),
        error: error => this.error.set(this.message(error)),
      });
  }

  selectPage(page: CuratedWikiPage): void {
    this.error.set(null);
    this.api.wikiPage(this.projectId(), page.slug, page.revision)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: selected => this.selectedPage.set(selected),
        error: error => this.error.set(this.message(error)),
      });
  }

  submitDecision(): void {
    const detail = this.selectedConflict();
    const rationale = this.rationale().trim();
    if (!detail || !rationale || this.actionPending()) return;
    const decisionId = crypto.randomUUID();
    this.actionPending.set(true);
    this.error.set(null);
    this.api.decide(this.projectId(), detail.conflict.conflict_id, {
      decision_id: decisionId,
      expected_version: detail.conflict.version,
      basis_digest: detail.conflict.basis_digest ?? '',
      decision: this.decision(),
      rationale,
      qualifiers: this.qualifiers().split('\n').map(item => item.trim()).filter(Boolean),
      writeback_requested: this.requestWriteback(),
    }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: conflict => {
        this.conflicts.update(items => items.map(item => item.conflict_id === conflict.conflict_id ? conflict : item));
        this.selectedConflict.update(current => current ? { ...current, conflict } : current);
        this.actionPending.set(false);
      },
      error: error => {
        this.actionPending.set(false);
        this.error.set(this.message(error));
      },
    });
  }

  loadCorrection(): void {
    const correctionId = this.correctionId().trim();
    if (!correctionId) return;
    this.error.set(null);
    this.api.correction(this.projectId(), correctionId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: detail => this.correctionDetail.set(detail),
        error: error => this.error.set(this.message(error)),
      });
  }

  approveCorrection(): void {
    const detail = this.correctionDetail();
    if (!detail || detail.state !== 'proposed' || detail.three_way.status !== 'clean' || this.actionPending()) return;
    this.actionPending.set(true);
    this.error.set(null);
    this.api.approveWriteback(
      this.projectId(),
      detail.proposal.correction_id,
      detail.proposal.proposal_digest
    ).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.actionPending.set(false);
        this.loadCorrection();
      },
      error: error => {
        this.actionPending.set(false);
        this.error.set(this.message(error));
      },
    });
  }

  displayCount(key: string): string {
    const snapshot = this.health();
    if (!snapshot) return 'unbekannt';
    const canonical = snapshot.counts[key];
    if (snapshot.coverage === 'complete' && canonical !== null && canonical !== undefined) {
      return String(canonical);
    }
    const observed = snapshot.counts[key + '_observed'];
    return observed === null || observed === undefined ? 'unbekannt' : 'mindestens ' + observed;
  }

  coverageLabel(coverage: KnowledgeCoverage): string {
    return coverage === 'complete' ? 'vollstaendig' : coverage === 'partial' ? 'teilweise' : 'unbekannt';
  }

  private load(projectId: string): void {
    if (!projectId) {
      this.error.set('Projekt-ID fehlt.');
      this.loading.set(false);
      return;
    }
    this.loading.set(true);
    this.error.set(null);
    forkJoin({
      health: this.api.health(projectId),
      conflicts: this.api.conflicts(projectId),
      wiki: this.api.wiki(projectId),
    }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => {
        this.health.set(result.health);
        this.conflicts.set(result.conflicts.items);
        this.wikiPages.set(result.wiki.items);
        const requestedTab = this.route.snapshot.queryParamMap.get('tab');
        if (requestedTab === 'wiki' || requestedTab === 'conflicts' || requestedTab === 'health') {
          this.activeTab.set(requestedTab);
        }
        const requestedConflict = this.route.snapshot.queryParamMap.get('conflict');
        const conflict = result.conflicts.items.find(item => item.conflict_id === requestedConflict);
        if (conflict) {
          this.activeTab.set('conflicts');
          this.selectConflict(conflict);
        }
        this.loading.set(false);
      },
      error: error => {
        this.loading.set(false);
        this.error.set(this.message(error));
      },
    });
  }

  private message(error: unknown): string {
    const value = error as { error?: { message?: string }; message?: string };
    return value.error?.message ?? value.message ?? 'Knowledge-Hygiene-Aufruf fehlgeschlagen.';
  }
}
