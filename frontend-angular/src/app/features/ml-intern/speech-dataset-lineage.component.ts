import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

export interface SpeechDatasetLineageNodeView {
  datasetId: string;
  version: string;
  parentVersion: string | null;
  manifestDigest: string;
  receiptId: string | null;
  contributorDigests: readonly string[];
  direction: string;
  consentDigest: string;
  fieldProvenanceDigests: readonly string[];
  createdByTaskId: string;
}

@Component({
  selector: 'app-speech-dataset-lineage',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section aria-labelledby="speech-lineage-title">
      <h2 id="speech-lineage-title">Speech-Dataset-Lineage</h2>
      <ol>
        @for (node of nodes; track node.datasetId + ':' + node.version) {
          <li>
            <h3>{{ node.datasetId }} · {{ node.version }}</h3>
            <p>Parent {{ node.parentVersion || 'Root' }} · Manifest {{ node.manifestDigest }}</p>
            <p>Richtung {{ node.direction }} · Consent {{ node.consentDigest }}</p>
            <p>Contributors {{ node.contributorDigests.join(', ') }}</p>
            <p>Feldprovenienz {{ node.fieldProvenanceDigests.join(', ') }}</p>
            <p>Hub-Task {{ node.createdByTaskId }} @if (node.receiptId) { · Receipt {{ node.receiptId }} }</p>
          </li>
        }
      </ol>
      <p>Parentversionen bleiben unverändert; Peer-Sync besitzt keine Split-, Training- oder Adapterlogik.</p>
    </section>
  `,
  styles: [`:host{display:block} li{border-left:3px solid #4f7cac;padding:.5rem 1rem;margin:.5rem 0} p{overflow-wrap:anywhere}`],
})
export class SpeechDatasetLineageComponent {
  @Input() nodes: readonly SpeechDatasetLineageNodeView[] = [];
}
