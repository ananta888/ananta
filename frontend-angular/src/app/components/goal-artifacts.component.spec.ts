import { buildProvenanceRows } from './goal-artifacts.component';

describe('GoalArtifactsComponent provenance rows', () => {
  it('maps usage refs to output rows', () => {
    const rows = buildProvenanceRows(
      [
        { usage_id: 'u-1', grant_id: 'g-1', artifact_ref: 'sources:keycloak:snap_1', usage_kind: 'embedded', task_id: 'task-a', worker_id: 'worker-a' },
      ],
      [
        { output_artifact_id: 'out-1', artifact_type: 'report', status: 'created', input_usage_refs: ['u-1'] },
      ],
    );

    expect(rows).toEqual([
      { from: 'sources:keycloak:snap_1', via: 'worker-a:task-a', to: 'out-1' },
    ]);
  });

  it('creates undocumented fallback when output has no input refs', () => {
    const rows = buildProvenanceRows(
      [],
      [{ output_artifact_id: 'out-2', artifact_type: 'report', status: 'created', worker_id: 'w-2', task_id: 't-2', input_usage_refs: [] }],
    );

    expect(rows).toEqual([
      { from: 'undocumented', via: 'w-2:t-2', to: 'out-2' },
    ]);
  });
});
