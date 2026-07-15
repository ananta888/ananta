import {
  VoiceApiError,
  VoiceConfigurationField,
  VoiceConfigurationSchema,
  VoiceJsonSchemaProperty,
  VoicePersonalizationImportPayload,
} from './voice.models';

export function voiceMutationKey(operation: string): string {
  const suffix = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `voice-ui:${operation}:${suffix}`;
}

export function voiceError(error: unknown): VoiceApiError {
  const candidate = (error as any)?.error?.data?.error
    ?? (error as any)?.error?.error
    ?? (error as any)?.error
    ?? error;
  const reportedCode = String(candidate?.code || '');
  const reportedMessage = String(candidate?.message || '');
  const browserName = String(candidate?.name || '');
  const localCode = reportedCode
    || (reportedMessage.startsWith('voice.') ? reportedMessage : '')
    || browserName;
  const localMessage = VOICE_CAPTURE_ERROR_MESSAGES[localCode];
  return {
    code: reportedCode || (localCode.startsWith('voice.') ? localCode : 'voice.ui_request_failed'),
    message: localMessage || reportedMessage || 'Voice-Anfrage fehlgeschlagen.',
    retriable: Boolean(candidate?.retriable),
  };
}

const VOICE_CAPTURE_ERROR_MESSAGES: Readonly<Record<string, string>> = {
  'voice.capture.system_audio_not_shared': 'Die Freigabe enthält keine Audiospur. Im Systemdialog „Audio teilen“ aktivieren und erneut starten.',
  'voice.capture.system_audio_not_supported': 'Dieser Browser oder dieses Gerät unterstützt keine direkte Systemaudio-Aufnahme.',
  'voice.capture.source_ended': 'Die Audiofreigabe wurde beendet. Die laufende Aufnahme wurde geschlossen.',
  'voice.capture.cancelled': 'Die Audioaufnahme wurde abgebrochen.',
  'voice.capture.microphone_permission_required': 'Für die Mikrofonaufnahme muss die Audioberechtigung erteilt werden.',
  'voice.capture.system_audio_permission_required': 'Für Systemaudio muss die Android-Wiedergabefreigabe erteilt werden.',
  'voice.capture.browser_not_supported': 'Dieser Browser stellt keinen unterstützten Audio-Capture-Adapter bereit.',
  'voice.recording.browser_not_supported': 'Dieser Browser unterstützt die lokale Audioaufnahme nicht.',
  NotAllowedError: 'Die Audio- oder Bildschirmfreigabe wurde nicht erteilt.',
  NotFoundError: 'Es wurde keine geeignete Audioquelle gefunden.',
  AbortError: 'Die Auswahl der Audioquelle wurde abgebrochen.',
  PLAYBACK_CAPTURE_UNSUPPORTED: 'Systemaudio benötigt Android 10 oder neuer.',
  PLAYBACK_CAPTURE_RECORD_AUDIO_PERMISSION_REQUIRED: 'Android benötigt die Audioberechtigung für die Wiedergabeaufnahme.',
  PLAYBACK_CAPTURE_CONSENT_DENIED: 'Die Android-Wiedergabefreigabe wurde nicht erteilt.',
  PLAYBACK_CAPTURE_CONSENT_EXPIRED: 'Die Android-Wiedergabefreigabe ist abgelaufen. Bitte die Aufnahme erneut starten.',
  PLAYBACK_CAPTURE_PREPARE_REQUIRED: 'Die Android-Wiedergabefreigabe fehlt oder ist abgelaufen. Bitte die Aufnahme erneut starten.',
};

export function validateVoiceField(field: VoiceConfigurationField, value: unknown): string | null {
  if (field.enabled === false) return field.reason_code || 'voice.configuration.field_disabled';
  if (value == null) return null;
  if (field.type === 'string_list') {
    if (!Array.isArray(value)) return 'voice.configuration.array_required';
    if (field.max_items != null && value.length > field.max_items) return 'voice.configuration.too_many_items';
    if (field.unique_items && new Set(value).size !== value.length) return 'voice.configuration.duplicate_items';
    if (field.enum?.length && value.some((item) => !field.enum!.includes(item as never))) {
      return 'voice.configuration.invalid_enum';
    }
    return null;
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return 'voice.configuration.number_required';
    if (field.minimum != null && value < field.minimum) return 'voice.configuration.below_minimum';
    if (field.maximum != null && value > field.maximum) return 'voice.configuration.above_maximum';
  }
  if (typeof value === 'string') {
    if (field.min_length != null && value.length < field.min_length) return 'voice.configuration.too_short';
    if (field.max_length != null && value.length > field.max_length) return 'voice.configuration.too_long';
  }
  const enabledOptions = (field.options || []).filter((option) => option.enabled !== false);
  if (enabledOptions.length && !enabledOptions.some((option) => option.value === value)) {
    return (field.options || []).find((option) => option.value === value)?.reason_code
      || 'voice.configuration.option_unavailable';
  }
  if (field.enum?.length && !field.enum.includes(value as never)) {
    return 'voice.configuration.invalid_enum';
  }
  return null;
}

function labelFor(key: string): string {
  return key.split('.').at(-1)!.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
}

function fieldFromProperty(
  key: string,
  property: VoiceJsonSchemaProperty,
  group: string,
  parent?: VoiceJsonSchemaProperty,
): VoiceConfigurationField {
  const type = property.type === 'array' ? 'string_list'
    : property.type === 'string' && property.enum?.length ? 'enum'
    : property.type;
  const scopes = property.scopes || parent?.scopes;
  const visibility = property.visibility || parent?.visibility;
  const requiredCapabilities = property.required_capabilities || parent?.required_capabilities;
  return {
    key,
    label: labelFor(key),
    description: property.description,
    group,
    type: type as VoiceConfigurationField['type'],
    default: property.default,
    enum: property.type === 'array' ? property.items?.enum : property.enum,
    minimum: property.minimum,
    maximum: property.maximum,
    min_length: property.minLength,
    max_length: property.maxLength,
    max_items: property.maxItems,
    unique_items: property.uniqueItems,
    scopes: scopes ? [...scopes] : undefined,
    visibility,
    visible: visibility !== 'hidden',
    secret_reference: property.secret_reference ?? parent?.secret_reference,
    required_capabilities: requiredCapabilities ? [...requiredCapabilities] : undefined,
    capability_reason_source: property.capability_reason_source || parent?.capability_reason_source,
  };
}

export function configurationFields(schema: VoiceConfigurationSchema | null): VoiceConfigurationField[] {
  if (!schema) return [];
  if (schema.fields?.length) return schema.fields;
  const fields: VoiceConfigurationField[] = [];
  for (const [key, property] of Object.entries(schema.properties || {})) {
    if (property.type === 'object' && property.properties) {
      for (const [childKey, child] of Object.entries(property.properties)) {
        fields.push(fieldFromProperty(`${key}.${childKey}`, child, labelFor(key), property));
      }
      continue;
    }
    fields.push(fieldFromProperty(key, property, 'Voice-Konfiguration'));
  }
  return fields;
}

export function valueAtPath(source: Record<string, unknown> | undefined, path: string): unknown {
  let current: unknown = source;
  for (const segment of path.split('.')) {
    if (!current || typeof current !== 'object') return undefined;
    current = (current as Record<string, unknown>)[segment];
  }
  return current;
}

export function hasPath(source: Record<string, unknown> | undefined, path: string): boolean {
  let current: unknown = source;
  const segments = path.split('.');
  for (let index = 0; index < segments.length; index += 1) {
    if (!current || typeof current !== 'object') return false;
    const record = current as Record<string, unknown>;
    if (!Object.prototype.hasOwnProperty.call(record, segments[index])) return false;
    current = record[segments[index]];
  }
  return true;
}

export function setAtPath(source: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
  const clone = structuredClone(source);
  const segments = path.split('.');
  let current = clone;
  for (const segment of segments.slice(0, -1)) {
    const existing = current[segment];
    current[segment] = existing && typeof existing === 'object'
      ? { ...(existing as Record<string, unknown>) }
      : {};
    current = current[segment] as Record<string, unknown>;
  }
  current[segments.at(-1)!] = value;
  return clone;
}

export function deleteAtPath(source: Record<string, unknown>, path: string): Record<string, unknown> {
  const clone = structuredClone(source);
  const segments = path.split('.');
  let current: Record<string, unknown> = clone;
  for (const segment of segments.slice(0, -1)) {
    const next = current[segment];
    if (!next || typeof next !== 'object') return clone;
    current = next as Record<string, unknown>;
  }
  delete current[segments.at(-1)!];
  return clone;
}

export type VoiceImportValidation =
  | { payload: VoicePersonalizationImportPayload; error: null }
  | { payload: null; error: VoiceApiError };

const importError = (code: string, message: string): VoiceImportValidation => ({
  payload: null,
  error: { code, message, retriable: false },
});

export function validatePersonalizationImport(rawJson: string, profileId: string): VoiceImportValidation {
  let raw: unknown;
  try {
    raw = JSON.parse(rawJson);
  } catch {
    return importError('voice_governance.invalid_json', 'Der Import muss ein gültiges JSON-Objekt sein.');
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return importError('voice_governance.invalid_json', 'Der Import muss ein JSON-Objekt sein.');
  }
  const payload = raw as Record<string, unknown>;
  if (payload['schema_version'] !== 'voice-personalization.v1') {
    return importError(
      'voice_personalization.invalid_import_schema',
      'Erwartet wird schema_version voice-personalization.v1.',
    );
  }
  if (String(payload['profile_id'] || '') !== profileId.trim()) {
    return importError(
      'voice_personalization.import_profile_mismatch',
      'Die Profil-ID im Import stimmt nicht mit dem Zielprofil überein.',
    );
  }
  const version = payload['version'];
  if (version != null && (typeof version !== 'number' || !Number.isInteger(version) || version < 0)) {
    return importError('voice_personalization.invalid_import_schema', 'version muss eine nichtnegative Ganzzahl sein.');
  }
  const items = payload['items'];
  if (!Array.isArray(items) || items.length > 500) {
    return importError(
      'voice_personalization.invalid_import_items',
      'items muss ein Array mit höchstens 500 Einträgen sein.',
    );
  }
  const kinds = new Set(['vocabulary', 'substitution', 'preference', 'negative']);
  const metadataKeys = new Set(['backend', 'domain', 'language', 'model_revision', 'reason_code']);
  for (const item of items) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      return importError('voice_personalization.invalid_import_item', 'Jeder Import-Eintrag muss ein Objekt sein.');
    }
    const entry = item as Record<string, unknown>;
    const kind = String(entry['kind'] || '').trim().toLowerCase();
    if (!kinds.has(kind)) {
      return importError('voice_personalization.invalid_kind', 'Ein Import-Eintrag besitzt einen unbekannten Typ.');
    }
    const source = entry['source_text'];
    const target = entry['target_text'];
    if ((source != null && typeof source !== 'string') || (target != null && typeof target !== 'string')) {
      return importError('voice_personalization.invalid_text_pair', 'source_text und target_text müssen Text oder null sein.');
    }
    if (String(source || '').length > 4000 || String(target || '').length > 4000) {
      return importError('voice_personalization.invalid_text_pair', 'Import-Texte dürfen höchstens 4000 Zeichen lang sein.');
    }
    const hasSource = Boolean(String(source || '').trim());
    const hasTarget = Boolean(String(target || '').trim());
    const validPair = (kind === 'vocabulary' && hasTarget)
      || (['preference', 'substitution'].includes(kind) && hasSource && hasTarget)
      || (kind === 'negative' && hasSource);
    if (!validPair) {
      return importError(
        'voice_personalization.invalid_text_pair',
        'Die Textfelder passen nicht zum Personalisierungstyp.',
      );
    }
    const metadata = entry['metadata'] ?? {};
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
      return importError('voice_personalization.invalid_metadata', 'metadata muss ein Objekt sein.');
    }
    for (const [key, value] of Object.entries(metadata as Record<string, unknown>)) {
      if (!metadataKeys.has(key) || (value != null && typeof value !== 'string') || String(value || '').length > 160) {
        return importError(
          'voice_personalization.invalid_metadata',
          'metadata enthält unbekannte oder ungültige Felder.',
        );
      }
    }
  }
  return { payload: raw as VoicePersonalizationImportPayload, error: null };
}
