import { Component, OnInit, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { CaseFlowApiService } from '../caseflow-api.service';
import { DiscoveryResult, SearchProfile } from '../caseflow.models';

@Component({
  standalone: true,
  selector: 'app-discovery-inbox',
  imports: [CommonModule, RouterModule],
  template: `
    <div class="discovery">
      <h2>Discovery Inbox</h2>

      <div class="profiles-section">
        <h3>Suchprofile</h3>
        @for (p of profiles(); track p.id) {
          <div class="profile-row">
            <span [class.disabled]="!p.enabled">{{ p.name }}</span>
            <button (click)="runProfile(p.id)" [disabled]="running()">Ausführen</button>
          </div>
        }
        @if (!profiles().length) {
          <p class="empty">Keine Suchprofile vorhanden.</p>
        }
      </div>

      @if (results().length) {
        <div class="results-section">
          <h3>Treffer ({{ results().length }})</h3>
          @for (r of results(); track r.id) {
            <div class="result-card" [class.ignored]="r.ignored" [class.duplicate]="r.is_duplicate">
              <div class="result-title">{{ r.title }}</div>
              <div class="result-meta">
                <span>{{ r.source_name }}</span>
                @if (r.source_url) { <a [href]="r.source_url" target="_blank">Link</a> }
                @if (r.is_duplicate) { <span class="tag dup">Duplikat</span> }
                @if (r.ignored) { <span class="tag ign">Ignoriert</span> }
                @if (r.converted_to_case_id) { <span class="tag conv">Case erstellt</span> }
              </div>
              @if (!r.ignored && !r.converted_to_case_id) {
                <div class="result-actions">
                  <button (click)="convert(r)">Case anlegen</button>
                  <button (click)="ignore(r)">Ignorieren</button>
                </div>
              }
            </div>
          }
        </div>
      }

      @if (message()) {
        <p class="message">{{ message() }}</p>
      }
    </div>
  `,
  styles: [`
    .discovery { padding: 1rem; }
    .profiles-section, .results-section { margin-bottom: 1.5rem; }
    .profile-row { display: flex; align-items: center; gap: 1rem; padding: 0.5rem 0; }
    .profile-row .disabled { color: #555; }
    .result-card { background: #1e1e1e; border-radius: 6px; padding: 0.75rem; margin-bottom: 0.5rem; }
    .result-card.ignored { opacity: 0.5; }
    .result-card.duplicate { border-left: 3px solid #f59e0b; }
    .result-title { font-weight: bold; margin-bottom: 0.25rem; }
    .result-meta { display: flex; gap: 0.75rem; font-size: 0.8rem; color: #aaa; margin-bottom: 0.5rem; }
    .result-meta a { color: #60a5fa; }
    .tag { border-radius: 4px; padding: 0.1rem 0.4rem; font-size: 0.75rem; }
    .tag.dup { background: #78350f; color: #fbbf24; }
    .tag.ign { background: #374151; }
    .tag.conv { background: #064e3b; color: #34d399; }
    .result-actions { display: flex; gap: 0.5rem; }
    button { background: #374151; border: none; color: #fff; padding: 0.3rem 0.7rem; border-radius: 4px; cursor: pointer; }
    button:hover { background: #4b5563; }
    button:disabled { opacity: 0.5; cursor: default; }
    .empty { color: #555; }
    .message { background: #1e3a2f; padding: 0.5rem 1rem; border-radius: 4px; }
  `],
})
export class DiscoveryInboxComponent implements OnInit {
  private readonly api = inject(CaseFlowApiService);

  profiles = signal<SearchProfile[]>([]);
  results = signal<DiscoveryResult[]>([]);
  running = signal(false);
  message = signal<string | null>(null);

  ngOnInit(): void {
    this.api.listSearchProfiles().subscribe({
      next: (p) => this.profiles.set(p),
      error: () => {},
    });
  }

  runProfile(profileId: string): void {
    this.running.set(true);
    this.api.runDiscovery(profileId).subscribe({
      next: (res) => {
        this.api.getRunResults(res.run_id).subscribe({
          next: (r) => { this.results.set(r); this.running.set(false); },
          error: () => this.running.set(false),
        });
      },
      error: () => { this.running.set(false); this.message.set('Discovery fehlgeschlagen.'); },
    });
  }

  convert(result: DiscoveryResult): void {
    const approvedBy = 'user';  // In real app: from auth context
    this.api.convertResult(result.id, 'job_application', approvedBy).subscribe({
      next: () => {
        this.message.set(`Case aus "${result.title}" erstellt.`);
        this.results.update(rs => rs.map(r => r.id === result.id ? { ...r, converted_to_case_id: 'created' } : r));
      },
      error: (err) => this.message.set(`Fehler: ${err.error?.detail ?? 'Unbekannt'}`),
    });
  }

  ignore(result: DiscoveryResult): void {
    this.api.ignoreResult(result.id).subscribe({
      next: () => {
        this.results.update(rs => rs.map(r => r.id === result.id ? { ...r, ignored: true } : r));
      },
      error: () => {},
    });
  }
}
