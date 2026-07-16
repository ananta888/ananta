import { apiErrorMessage, boundedText, boundedTrainingLog, shortHash, trainingStatusTone } from './model-training-status';

describe('model training presentation safety', () => {
  it('redacts common credentials and bounds untrusted event text', () => {
    const value = boundedText('Bearer secret-token password=hunter2 token: abcdef ' + 'x'.repeat(100), 80);

    expect(value).not.toContain('secret-token');
    expect(value).not.toContain('hunter2');
    expect(value).not.toContain('abcdef');
    expect(value.length).toBeLessThanOrEqual(81);
  });

  it('maps lifecycle states and shortens hashes without inventing values', () => {
    expect(trainingStatusTone('completed')).toBe('success');
    expect(trainingStatusTone('failed')).toBe('error');
    expect(trainingStatusTone('queued')).toBe('warning');
    expect(shortHash('1234567890abcdef')).toBe('1234567890ab');
    expect(shortHash(undefined)).toBe('-');
  });

  it.each([
    [401, 'neu anmelden'],
    [403, 'Administratorrechte'],
    [409, 'Daten aktualisieren'],
    [413, 'Größenlimit'],
    [422, 'erneut validieren'],
    [503, 'vorübergehend nicht verfügbar'],
  ])('provides a visible recovery instruction for HTTP %s', (status, expected) => {
    expect(apiErrorMessage({ status }, 'Aktion fehlgeschlagen.')).toContain(expected);
  });

  it('preserves nested dataset_referenced and explicitly rejects a force-delete recovery', () => {
    const message = apiErrorMessage({
      status: 409,
      error: { status: 'error', data: { error: { code: 'dataset_referenced', message: 'referenced datasets cannot be deleted' } } },
    }, 'Dataset konnte nicht gelöscht werden.');

    expect(message).toContain('dataset_referenced');
    expect(message).toContain('Referenzen zuerst regulär auflösen');
    expect(message).toContain('Force-Delete wird nicht angeboten');
  });

  it('removes credentials and training records from bounded job logs', () => {
    const safe = boundedTrainingLog('{"instruction":"private prompt","output":"private answer","token":"secret"}');

    expect(safe).not.toContain('private prompt');
    expect(safe).not.toContain('private answer');
    expect(safe).not.toContain('secret');
    expect(safe).toContain('[TRAINING_DATA_REDACTED]');
  });
});
