import { Component, Injectable, Input, OnInit, inject, signal } from '@angular/core';
import { finalize } from 'rxjs';

import { ModelCatalogClient, CognitiveStyleReadModel } from '../model-dashboard/model-catalog.client';
import { SystemFacade } from '../system.facade';

@Injectable({ providedIn: 'root' })
export class CognitiveStyleUiStore {
  private readonly client = inject(ModelCatalogClient);
  private readonly system = inject(SystemFacade);
  private requested = false;

  readonly readModel = signal<CognitiveStyleReadModel | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  load(): void {
    if (this.requested) return;
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) {
      this.error.set('Kein Hub für Cognitive-Style-Projektion verfügbar.');
      return;
    }
    this.requested = true;
    this.loading.set(true);
    this.client.readCognitiveStyles(hub.url).pipe(
      finalize(() => this.loading.set(false)),
    ).subscribe({
      next: value => this.readModel.set(value),
      error: () => this.error.set('Cognitive-Style-Projektion nicht verfügbar.'),
    });
  }
}

@Component({
  standalone: true,
  selector: 'app-cognitive-style-summary',
  template: `
    <section class="style-summary" aria-label="Cognitive-Style-Profil">
      <header>
        <strong>Cognitive Style</strong>
        <a href="/settings?section=models">Details unter Modelle</a>
      </header>
      @if (store.loading()) { <p role="status">Style-Projektion wird geladen …</p> }
      @if (profile(); as measured) {
        <dl>
          <div><dt>Regel/Korrektheit</dt><dd><progress max="1" [value]="measured.scores.rule_correctness"></progress> {{ percent(measured.scores.rule_correctness) }}</dd></div>
          <div><dt>Wahrheit/Exploration</dt><dd><progress max="1" [value]="measured.scores.truth_exploration"></progress> {{ percent(measured.scores.truth_exploration) }}</dd></div>
          <div><dt>Initiative/Assertivität</dt><dd><progress max="1" [value]="measured.scores.initiative_assertiveness"></progress> {{ percent(measured.scores.initiative_assertiveness) }}</dd></div>
        </dl>
        <p>Confidence {{ percent(measured.confidence) }} · {{ measured.sample_count }} Samples · gemessen {{ measured.measured_at }}</p>
      } @else if (!store.loading()) {
        <p>Kein gemessenes Profil für {{ modelProfileId || 'diesen Agenten' }} zugeordnet.</p>
      }
      @if (target(); as desired) {
        <p><strong>Rollenziel {{ desired.role_id }}:</strong> {{ desired.rationale }}</p>
        <p>Zielbereiche: Regel {{ range(desired.rule_correctness) }}, Exploration {{ range(desired.truth_exploration) }}, Initiative {{ range(desired.initiative_assertiveness) }}.</p>
        @if (profile(); as measured) {
          <p><strong>Style-Beitrag:</strong> {{ fitExplanation(measured.scores, desired) }}</p>
        }
      } @else if (roleId) {
        <p>Kein Style-Ziel für Rolle {{ roleId }} gefunden.</p>
      }
      <p class="boundary">Style ist ein weicher Rankingbeitrag nach Capability-, Permission- und Security-Gates und verleiht keine Rechte.</p>
      @if (store.error()) { <p class="warning" role="status">{{ store.error() }}</p> }
    </section>
  `,
  styles: [`
    .style-summary { background: #f1f6f4; border: 1px solid #b8c8c9; border-radius: .65rem; margin-top: .75rem; padding: .75rem; }
    header { align-items: center; display: flex; justify-content: space-between; }
    dl { display: grid; gap: .35rem; margin: .6rem 0; }
    dl div { display: grid; grid-template-columns: minmax(10rem, .8fr) 1fr; }
    dt { font-weight: 600; } dd { margin: 0; } progress { width: min(10rem, 65%); }
    p { font-size: .8rem; margin: .35rem 0 0; }
    .boundary { color: #36565c; } .warning { color: #8b3d12; }
  `],
})
export class CognitiveStyleSummaryComponent implements OnInit {
  readonly store = inject(CognitiveStyleUiStore);
  @Input() roleId = '';
  @Input() modelProfileId = '';

  profile() {
    return this.store.readModel()?.configuration.profiles.find(
      item => item.model_profile_id === this.modelProfileId,
    );
  }

  target() {
    const normalized = this.normalizedRole();
    const targets = this.store.readModel()?.configuration.role_targets ?? [];
    return targets.find(item => item.role_id === normalized && !item.project_id && !item.organization_id);
  }

  ngOnInit(): void {
    this.store.load();
  }

  percent(value: number): string {
    return `${Math.round(value * 100)}%`;
  }

  range(value: { minimum: number; maximum: number }): string {
    return `${this.percent(value.minimum)}–${this.percent(value.maximum)}`;
  }

  fitExplanation(
    scores: { rule_correctness: number; truth_exploration: number; initiative_assertiveness: number },
    target: {
      rule_correctness: { minimum: number; maximum: number };
      truth_exploration: { minimum: number; maximum: number };
      initiative_assertiveness: { minimum: number; maximum: number };
    },
  ): string {
    const dimensions = [
      ['Regel/Korrektheit', scores.rule_correctness, target.rule_correctness],
      ['Wahrheit/Exploration', scores.truth_exploration, target.truth_exploration],
      ['Initiative/Assertivität', scores.initiative_assertiveness, target.initiative_assertiveness],
    ] as const;
    const misses = dimensions
      .filter(([, score, range]) => score < range.minimum || score > range.maximum)
      .map(([label]) => label);
    const matches = dimensions.length - misses.length;
    return misses.length
      ? `${matches}/3 Zielbereiche getroffen; außerhalb: ${misses.join(', ')}. Der Hub bewertet diesen Beitrag erst nach allen harten Gates.`
      : '3/3 Zielbereiche getroffen. Der Hub bewertet diesen Beitrag erst nach allen harten Gates.';
  }

  private normalizedRole(): string {
    const role = String(this.roleId || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
    return ({ coder: 'implementer', developer: 'developer', reasoning: 'reviewer' } as Record<string, string>)[role] || role;
  }
}
