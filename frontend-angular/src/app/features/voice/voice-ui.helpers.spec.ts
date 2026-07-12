import {
  configurationFields,
  deleteAtPath,
  hasPath,
  setAtPath,
  validateVoiceField,
  validatePersonalizationImport,
  valueAtPath,
  voiceError,
} from './voice-ui.helpers';

describe('voice UI contract helpers', () => {
  it('derives all controls and enum values from the canonical JSON schema', () => {
    const fields = configurationFields({
      schema_version: 'ananta.voice-configuration.v1',
      type: 'object',
      properties: {
        recognition_strategy: {
          type: 'string', enum: ['single', 'parallel_compare'], default: 'single',
          scopes: ['global', 'profile', 'session'], visibility: 'standard', secret_reference: false,
          required_capabilities: ['voice_fusion'],
          capability_reason_source: '/v1/voice/capabilities/model_catalog',
        },
        secondary_backends: {
          type: 'array', maxItems: 3, uniqueItems: true,
          items: { type: 'string', enum: ['vosk', 'whisper_cpp'] },
          scopes: ['global', 'profile', 'session'], visibility: 'standard', secret_reference: false,
        },
        feature_flags: {
          type: 'object', additionalProperties: false,
          scopes: ['global', 'profile', 'session'], visibility: 'advanced', secret_reference: false,
          properties: { restricted_worker: { type: 'boolean', default: false } },
        },
      },
    });

    expect(fields.map((field) => field.key)).toEqual([
      'recognition_strategy', 'secondary_backends', 'feature_flags.restricted_worker',
    ]);
    expect(fields[0].enum).toEqual(['single', 'parallel_compare']);
    expect(fields[1].type).toBe('string_list');
    expect(fields[1].enum).toEqual(['vosk', 'whisper_cpp']);
    expect(fields[0]).toEqual(expect.objectContaining({
      scopes: ['global', 'profile', 'session'],
      visibility: 'standard',
      secret_reference: false,
      required_capabilities: ['voice_fusion'],
      capability_reason_source: '/v1/voice/capabilities/model_catalog',
    }));
    expect(fields[2]).toEqual(expect.objectContaining({
      scopes: ['global', 'profile', 'session'], visibility: 'advanced', secret_reference: false,
    }));
  });

  it('updates and deletes nested sparse deltas without mutating the input', () => {
    const original = { feature_flags: { voice_fusion: false } };
    const updated = setAtPath(original, 'feature_flags.restricted_worker', true);

    expect(valueAtPath(updated, 'feature_flags.restricted_worker')).toBe(true);
    expect(hasPath(original, 'feature_flags.restricted_worker')).toBe(false);
    expect(deleteAtPath(updated, 'feature_flags.restricted_worker')).toEqual(original);
  });

  it('uses contract reason codes for invalid values', () => {
    expect(validateVoiceField({ key: 'confidence', type: 'number', minimum: 0, maximum: 1 }, 2))
      .toBe('voice.configuration.above_maximum');
  });

  it('extracts stable Hub error codes from enveloped HTTP failures', () => {
    expect(voiceError({ error: { data: { error: { code: 'policy_blocked', message: 'blocked' } } } }))
      .toEqual({ code: 'policy_blocked', message: 'blocked', retriable: false });
  });

  it('validates portable personalization imports before they reach the Hub', () => {
    const valid = validatePersonalizationImport(JSON.stringify({
      schema_version: 'voice-personalization.v1',
      profile_id: 'profile-a',
      version: 2,
      items: [
        { id: 'exported-id', kind: 'vocabulary', target_text: 'Ananta', metadata: { language: 'de' } },
        { kind: 'substitution', source_text: 'Anantha', target_text: 'Ananta', metadata: {} },
      ],
    }), 'profile-a');

    expect(valid.error).toBeNull();
    expect(valid.payload?.items).toHaveLength(2);
  });

  it('rejects import schema, profile and metadata mismatches with Hub reason codes', () => {
    expect(validatePersonalizationImport('{broken', 'profile-a').error?.code)
      .toBe('voice_governance.invalid_json');
    expect(validatePersonalizationImport(JSON.stringify({
      schema_version: 'voice-personalization.v1', profile_id: 'other', items: [],
    }), 'profile-a').error?.code).toBe('voice_personalization.import_profile_mismatch');
    expect(validatePersonalizationImport(JSON.stringify({
      schema_version: 'voice-personalization.v1', profile_id: 'profile-a',
      items: [{ kind: 'vocabulary', target_text: 'Ananta', metadata: { secret: 'no' } }],
    }), 'profile-a').error?.code).toBe('voice_personalization.invalid_metadata');
  });
});
