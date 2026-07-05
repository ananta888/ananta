import { Component, Input } from '@angular/core';
import { SourceItem, SourceSnapshot } from '../../services/sources.service';

@Component({
  standalone: true,
  selector: 'app-source-citation-panel',
  template: `
    @if (source) {
      <div class="citation-panel">
        <p>Kind: {{ source.extensions?.['record_kind'] || 'primary_source' }}</p>
        <p>Derived insights: {{ source.extensions?.['record_counts']?.derived_insights || 0 }}</p>
        <p>License: {{ provenance?.license_status || source.citation_source?.license_ref || 'unknown' }}</p>
        <p>Origin: {{ provenance?.original_url || provenance?.original_file_path || source.citation_source?.canonical_url || '-' }}</p>
        @for (snapshot of snapshots; track snapshot.snapshot_id) {
          <p>{{ snapshot.snapshot_id }} · {{ snapshot.content_hash || '-' }} · {{ snapshot.extensions?.['imported_at'] || snapshot.retrieved_at || '-' }} · {{ snapshot.status }}</p>
        }
      </div>
    }
  `,
})
export class SourceCitationPanelComponent {
  @Input() source: SourceItem | null = null;
  @Input() snapshots: SourceSnapshot[] = [];

  get provenance(): any {
    return this.snapshots[0]?.extensions?.['provenance'] || this.source?.extensions?.['provenance'] || null;
  }
}
