import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { KnowledgeExpertApiService } from './knowledge-expert-api.service';
import { KnowledgeExpertBankView, KnowledgeExpertControlSnapshot } from './knowledge-expert.models';

@Component({
  selector: 'app-knowledge-expert-control',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section data-testid="knowledge-expert-control">
      <header class="header">
        <div>
          <p class="eyebrow">Hub Control Plane</p>
          <h2>Parametrische Knowledge Experts</h2>
          <p>Aktive Generationen, Provenance und Rollout-Gates. RAG bleibt der sichere Fallback.</p>
        </div>
        <button type="button" (click)="load()" [disabled]="loading">Aktualisieren</button>
      </header>

      @if (error) { <p class="danger" role="alert">{{ error }}</p> }
      @if (snapshot) {
        <div class="summary">
          <span [class.disabled]="!snapshot.enabled">{{ snapshot.enabled ? 'Expert-System aktivierbar' : 'Default-off' }}</span>
          <span>Rollout: {{ snapshot.rollout_state }}</span>
          <span>Fallback: {{ snapshot.fallback_mode }}</span>
        </div>
        <section>
          <h3>Promotion-Gates</h3>
          <ul>
            @for (gate of gateEntries(); track gate[0]) {
              <li><strong>{{ gate[0] }}</strong>: {{ gate[1] }}</li>
            }
          </ul>
        </section>
        <section>
          <h3>Aktive Banken</h3>
          @if (!snapshot.active_banks.length) { <p>Keine aktive Expert-Bank.</p> }
          @for (bank of snapshot.active_banks; track bank.bank_id + bank.generation_id) {
            <article class="bank">
              <strong>{{ bank.bank_id }} · {{ bank.generation_id }}</strong>
              <span>{{ bank.expert_count }} Experten · {{ bank.status }}</span>
              <small>Policy {{ bank.policy_digest }}</small>
              @if (bank.provenance) {
                <small>{{ bank.provenance.knowledge_unit_count }} Units / {{ bank.provenance.source_count }} Quellen</small>
              }
              <button type="button" (click)="prepare('disable', bank)">Deaktivieren</button>
              <button type="button" (click)="prepare('revoke', bank)">Widerruf vorbereiten</button>
              @if (bank.previous_generation_id) {
                <button type="button" (click)="prepare('rollback', bank)">Rollback vorbereiten</button>
              }
            </article>
          }
        </section>
        <section>
          <h3>Kandidaten</h3>
          @for (bank of snapshot.candidate_banks; track bank.bank_id + bank.generation_id) {
            <article class="bank">
              <strong>{{ bank.bank_id }} · {{ bank.generation_id }}</strong>
              <span>{{ bank.expert_count }} Experten · {{ bank.status }}</span>
              <button type="button" (click)="prepare('activate', bank)" [disabled]="!snapshot.enabled">
                Aktivierung vorbereiten
              </button>
              <button type="button" (click)="prepare('revoke', bank)">Widerruf vorbereiten</button>
            </article>
          }
        </section>
      }

      @if (selected) {
        <form (submit)="$event.preventDefault(); submit()" class="confirm">
          <strong>{{ action }} · {{ selected.bank_id }}</strong>
          <label>Begründung <textarea name="reason" [(ngModel)]="reason" maxlength="1000" required></textarea></label>
          <label><input type="checkbox" name="confirmed" [(ngModel)]="confirmed" /> Auswirkung verstanden</label>
          <button type="submit" [disabled]="loading || !confirmed || !reason.trim()">Hub-Task absenden</button>
          <button type="button" (click)="selected = null">Abbrechen</button>
        </form>
      }
    </section>
  `,
  styles: [`
    .header,.summary,.bank{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
    .summary,.bank,.confirm{border:1px solid #334155;border-radius:10px;padding:12px;margin:12px 0}
    .bank small{display:block;color:#94a3b8}.eyebrow{color:#60a5fa}.danger{color:#fca5a5}.disabled{color:#fbbf24}
    .confirm label{display:block;margin:8px 0}.confirm textarea{display:block;width:100%;min-height:70px}
  `],
})
export class KnowledgeExpertControlComponent implements OnInit {
  private readonly api = inject(KnowledgeExpertApiService);
  private readonly state = inject(ControlCenterStateFacade);
  private readonly cdr = inject(ChangeDetectorRef);
  snapshot: KnowledgeExpertControlSnapshot | null = null;
  selected: KnowledgeExpertBankView | null = null;
  action: 'activate' | 'rollback' | 'revoke' | 'disable' = 'activate';
  reason = '';
  confirmed = false;
  loading = false;
  error = '';

  ngOnInit(): void { this.load(); }

  load(): void {
    const hubUrl = this.state.hubBaseUrl();
    if (!hubUrl) { this.error = 'Hub nicht verfügbar'; return; }
    this.loading = true;
    this.api.snapshot(hubUrl).pipe(finalize(() => { this.loading = false; this.cdr.markForCheck(); }))
      .subscribe({ next: value => { this.snapshot = value; this.error = ''; }, error: () => { this.error = 'Expert-Status nicht verfügbar'; } });
  }

  gateEntries(): [string, string][] { return Object.entries(this.snapshot?.gates || {}).sort(); }

  prepare(action: 'activate' | 'rollback' | 'revoke' | 'disable', bank: KnowledgeExpertBankView): void {
    this.action = action; this.selected = bank; this.reason = ''; this.confirmed = false;
  }

  submit(): void {
    const hubUrl = this.state.hubBaseUrl();
    if (!hubUrl || !this.selected || !this.confirmed || !this.reason.trim()) return;
    const generation = this.action === 'rollback'
      ? String(this.selected.previous_generation_id || '') : this.selected.generation_id;
    const activeGeneration = this.snapshot?.active_banks
      .find(bank => bank.bank_id === this.selected?.bank_id)?.generation_id || '';
    this.loading = true;
    this.api.command(hubUrl, {
      schema: 'ananta.knowledge-expert-control-command.v1', action: this.action,
      bank_id: this.selected.bank_id, generation_id: generation,
      expected_generation_id: this.action === 'activate' ? activeGeneration : this.selected.generation_id,
      reason: this.reason.trim(), confirmed: true,
    }).pipe(finalize(() => { this.loading = false; this.cdr.markForCheck(); }))
      .subscribe({ next: () => { this.selected = null; this.load(); }, error: () => { this.error = 'Hub-Command abgewiesen'; } });
  }
}
