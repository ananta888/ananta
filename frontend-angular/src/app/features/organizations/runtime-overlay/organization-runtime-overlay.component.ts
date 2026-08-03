import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';

import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';

@Component({
  selector: 'app-organization-runtime-overlay',
  standalone: true,
  imports: [CommonModule],
  template: `
    @if (state.topology()?.runtime_overlay; as overlay) {
      <section class="overlay" aria-labelledby="runtime-overlay-heading">
        <div>
          <h3 id="runtime-overlay-heading">Runtime-Overlay</h3>
          <p>{{ overlay.generated_at | date:'medium' }} · Snapshot <code>{{ overlay.snapshot_hash }}</code></p>
        </div>
        @if (overlay.stale || state.revisionMismatch()) {
          <strong class="stale" role="alert">Stale / Revisionsabweichung – Runtime-Daten werden nicht auf die Definition geschrieben.</strong>
        } @else {
          <strong class="current">revisionsgebunden</strong>
        }
      </section>
    }
  `,
  styles: [`
    .overlay { align-items: center; background: #0e1a2e; border: 1px solid #2d4467; border-radius: .6rem; display: flex; gap: 1rem; justify-content: space-between; padding: .65rem .8rem; }
    h3, p { margin: 0; } h3 { font-size: .9rem; } p { color: #91a5c7; font-size: .72rem; overflow-wrap: anywhere; }
    .current, .stale { border: 1px solid; border-radius: 999px; font-size: .7rem; padding: .25rem .55rem; }
    .current { border-color: #58b584; color: #91e4b5; } .stale { border-color: #e46d77; color: #ffc1c7; }
  `],
})
export class OrganizationRuntimeOverlayComponent {
  readonly state = inject(OrganizationTopologyStateService);
}
