import {
  PeerEvidenceLineageView,
  PeerEvidenceQuarantineView,
  PeerEvidenceSyncView,
} from './peer-evidence-sync-panel.component';
import {
  PeerTranscriptCandidateView,
  PeerTranscriptRegionView,
} from './peer-transcript-conflict-panel.component';

export type PeerEvidenceEvidenceProjection = Pick<
  PeerEvidenceSyncView,
  'quarantine' | 'quarantineCount' | 'lineage' | 'candidates' | 'regions' | 'resolutionHash' | 'resolutionPolicyVersion'
>;

/**
 * Mutable holder of the evidence-derived panel rows (quarantine, lineage,
 * conflict candidates/regions, resolution binding). The facade replaces the
 * immutable row arrays; this class only resets and projects them.
 */
export class PeerEvidenceViewState {
  quarantineRows: readonly PeerEvidenceQuarantineView[] = Object.freeze([]);
  lineage: readonly PeerEvidenceLineageView[] = Object.freeze([]);
  conflictCandidates: readonly PeerTranscriptCandidateView[] = Object.freeze([]);
  conflictRegions: readonly PeerTranscriptRegionView[] = Object.freeze([]);
  resolutionHash = '';
  resolutionPolicyVersion = '';

  reset(): void {
    this.quarantineRows = Object.freeze([]);
    this.lineage = Object.freeze([]);
    this.conflictCandidates = Object.freeze([]);
    this.conflictRegions = Object.freeze([]);
    this.resolutionHash = '';
    this.resolutionPolicyVersion = '';
  }

  project(): PeerEvidenceEvidenceProjection {
    return {
      quarantine: this.quarantineRows,
      quarantineCount: this.quarantineRows.filter(value => value.state === 'quarantined' || value.state === 'conflict').length,
      lineage: this.lineage,
      candidates: this.conflictCandidates,
      regions: this.conflictRegions,
      resolutionHash: this.resolutionHash,
      resolutionPolicyVersion: this.resolutionPolicyVersion,
    };
  }
}
