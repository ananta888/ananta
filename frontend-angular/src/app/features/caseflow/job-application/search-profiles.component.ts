import { Component, OnInit, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { CaseFlowApiService } from '../caseflow-api.service';
import { SearchProfile } from '../caseflow.models';

@Component({
  standalone: true,
  selector: 'app-search-profiles',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="profiles">
      <h2>Suchprofile</h2>
      <div class="create-form">
        <input [(ngModel)]="newName" placeholder="Profilname" />
        <button (click)="create()">Erstellen</button>
      </div>
      @for (p of profiles(); track p.id) {
        <div class="profile-item" [class.disabled]="!p.enabled">
          <span>{{ p.name }}</span>
          <span class="type">{{ p.profile_type }}</span>
          <span class="status">{{ p.enabled ? 'Aktiv' : 'Deaktiviert' }}</span>
        </div>
      }
      @if (!profiles().length) {
        <p>Keine Suchprofile.</p>
      }
    </div>
  `,
  styles: [`
    .profiles { padding: 1rem; }
    .create-form { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
    input { background: #2a2a2a; border: 1px solid #444; color: #fff; padding: 0.4rem 0.8rem; border-radius: 4px; }
    button { background: #374151; border: none; color: #fff; padding: 0.4rem 0.8rem; border-radius: 4px; cursor: pointer; }
    .profile-item { display: flex; gap: 1rem; align-items: center; padding: 0.5rem; background: #1e1e1e; border-radius: 4px; margin-bottom: 0.5rem; }
    .profile-item.disabled { opacity: 0.5; }
    .type, .status { font-size: 0.8rem; color: #aaa; }
  `],
})
export class SearchProfilesComponent implements OnInit {
  private readonly api = inject(CaseFlowApiService);
  profiles = signal<SearchProfile[]>([]);
  newName = '';

  ngOnInit(): void {
    this.api.listSearchProfiles().subscribe({
      next: (p) => this.profiles.set(p),
      error: () => {},
    });
  }

  create(): void {
    if (!this.newName.trim()) return;
    this.api.createSearchProfile({ name: this.newName.trim() }).subscribe({
      next: (p) => { this.profiles.update(ps => [...ps, p]); this.newName = ''; },
      error: () => {},
    });
  }
}
