import {
  parseSourceControlIndexAccessPreparation,
  parseSourceControlIndexAccessResult,
} from './source-control-index-access.model';

describe('source-control index access aggregate contract', () => {
  it('accepts only the local redacted one-time preparation', () => {
    const parsed = parseSourceControlIndexAccessPreparation(preparation());

    expect(parsed.readiness.ready).toBe(true);
    expect(parsed.options[0].effect).toEqual({
      provider_location: 'local', transformation: 'redacted', one_time: true,
    });
  });

  it('fails closed for cloud, non-redacted or expanded response fields', () => {
    const cloud = preparation();
    cloud.options[0].effect.provider_location = 'cloud';
    expect(() => parseSourceControlIndexAccessPreparation(cloud)).toThrow('unsafe');

    const expanded = preparation();
    (expanded as any).policy_id = 'browser-visible-policy';
    expect(() => parseSourceControlIndexAccessPreparation(expanded)).toThrow('keys_invalid');
  });

  it('requires the single documented next action after the aggregate command', () => {
    const parsed = parseSourceControlIndexAccessResult(result());
    expect(parsed.next_actions).toEqual(['start_index_run']);

    const unsafe = result();
    unsafe.next_actions = ['start_index_run', 'write_source'];
    expect(() => parseSourceControlIndexAccessResult(unsafe)).toThrow('next_actions_invalid');
  });
});

function preparation(): any {
  return {
    connection_id: 'connection-example',
    source_revision: {
      source_revision_id: 'source-revision-example',
      revision_digest: '1'.repeat(64),
      admission_state: 'admitted',
      captured_at: '2026-08-01T12:00:00Z',
    },
    destinations: [{
      destination_id: 'destination-example',
      worker_id: 'worker-example',
      runtime_kind: 'codecompass',
      provider_location: 'local_container',
      data_residency: 'local',
    }],
    options: [{
      option_id: 'redacted-local-once',
      preset_id: 'preset-redacted-index',
      label: 'Lokal redigiert',
      effect: { provider_location: 'local', transformation: 'redacted', one_time: true },
      duration_seconds: { minimum: 60, maximum: 900, default: 900 },
    }],
    readiness: { ready: true, reason_codes: [] },
    etag: 'a'.repeat(64),
  };
}

function result(): any {
  return {
    access_ready: true,
    connection_id: 'connection-example',
    source_revision_id: 'source-revision-example',
    destination_id: 'destination-example',
    option_id: 'redacted-local-once',
    effect: { provider_location: 'local', transformation: 'redacted', one_time: true },
    policy: { policy_id: 'policy-example', version: 1, state: 'active', etag: 'b'.repeat(64) },
    grant: { grant_id: 'grant-example', state: 'active', etag: 'c'.repeat(64), expires_at: '2026-08-01T12:15:00Z' },
    next_actions: ['start_index_run'],
  };
}
