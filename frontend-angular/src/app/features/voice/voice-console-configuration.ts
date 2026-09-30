import {
  buildCorrectorModels,
  correctorProviderSupportsManual,
  isReportedCorrectorModel,
  isVoiceCorrectionModel,
  VoiceChoice,
} from './voice-corrector-catalog';
import { configurationFields, valueAtPath } from './voice-ui.helpers';
import {
  VoiceCapabilityStatus,
  VoiceConfiguration,
  VoiceConfigurationSchema,
} from './voice.models';

/**
 * Pure mapping between the Hub voice configuration (schema, layered sources,
 * effective values, runtime capabilities) and the console's editable
 * backend/corrector selection.
 */

export type VoiceConfigurationTarget = 'profile' | 'session';

export interface VoiceConsoleSelection {
  selectedRecognitionStrategy: string;
  selectedBackend: string;
  selectedSecondaryBackend: string;
  generativeCorrection: boolean;
  selectedCorrectorProvider: string;
  selectedCorrectorModel: string;
  manualCorrectorModel: boolean;
  manualCorrectorModelId: string;
}

export function voiceFieldChoices(
  schema: VoiceConfigurationSchema | null,
  key: string,
  fallback: string[],
): VoiceChoice[] {
  const field = configurationFields(schema).find((candidate) => candidate.key === key);
  if (!field) return fallback.map((id) => ({ id, label: id, available: true, reason: '' }));
  const values = field.options?.map((option) => ({
    id: String(option.value),
    label: option.label || String(option.value),
    available: option.enabled !== false,
    reason: String(option.reason_code || ''),
  })) || (field.enum || []).map((value) => ({
    id: String(value), label: String(value), available: true, reason: '',
  }));
  return uniqueChoices(values);
}

export function voiceCorrectorModels(
  capabilities: VoiceCapabilityStatus | null,
  configuration: VoiceConfiguration | null,
  schema: VoiceConfigurationSchema | null,
  providerId: string,
): VoiceChoice[] {
  return buildCorrectorModels(
    capabilities,
    configuration,
    voiceFieldChoices(schema, 'generative_corrector_model', []),
    providerId,
  );
}

export function voiceAsrBackends(
  schema: VoiceConfigurationSchema | null,
  capabilities: VoiceCapabilityStatus | null,
): VoiceChoice[] {
  const schemaChoices = voiceFieldChoices(schema, 'primary_backend', []);
  const runtimeModels = [
    ...(capabilities?.models || []),
    ...(capabilities?.model_catalog || []),
  ].filter((model) => !isVoiceCorrectionModel(model));
  const runtimeChoices = runtimeModels.map((model) => ({
    id: String(model.backend || model.engine || model.id),
    label: String(model.backend || model.engine || model.id),
    available: modelIsAvailable(model),
    reason: String(model.reason_code || (modelIsAvailable(model) ? '' : model.status || 'voice.backend.unavailable')),
  }));
  const ids = new Set([
    ...schemaChoices.map((choice) => choice.id),
    ...runtimeChoices.map((choice) => choice.id),
  ]);
  return [...ids].map((id) => {
    const schemaChoice = schemaChoices.find((choice) => choice.id === id);
    const matching = runtimeChoices.filter((choice) => choice.id === id);
    const ready = matching.find((choice) => choice.available);
    const unavailable = matching.find((choice) => !choice.available);
    return {
      id,
      label: asrBackendLabel(id, schemaChoice?.label || ready?.label || unavailable?.label),
      available: schemaChoice?.available !== false && Boolean(ready),
      reason: schemaChoice?.reason || ready?.reason || unavailable?.reason || 'voice.backend.not_reported',
    };
  });
}

/** Editable selection derived from the effective configuration the Hub reports. */
export function effectiveVoiceSelection(
  configuration: VoiceConfiguration,
  capabilities: VoiceCapabilityStatus | null,
  schema: VoiceConfigurationSchema | null,
): VoiceConsoleSelection {
  const effective = configuration.effective || {};
  const secondary = valueAtPath(effective, 'secondary_backends');
  const selection: VoiceConsoleSelection = {
    selectedRecognitionStrategy: String(valueAtPath(effective, 'recognition_strategy') || 'single'),
    selectedBackend: String(valueAtPath(effective, 'primary_backend') || 'vosk'),
    selectedSecondaryBackend: Array.isArray(secondary) ? String(secondary[0] || '') : 'whisper_cpp',
    generativeCorrection: String(valueAtPath(effective, 'correction_policy') || '') === 'generative_rewrite'
      || valueAtPath(effective, 'feature_flags.generative_corrector') === true,
    selectedCorrectorProvider: String(
      valueAtPath(effective, 'generative_corrector_provider') || 'embedded',
    ).trim().toLowerCase() || 'embedded',
    selectedCorrectorModel: String(valueAtPath(effective, 'generative_corrector_model') || ''),
    manualCorrectorModel: false,
    manualCorrectorModelId: '',
  };
  if (selection.selectedCorrectorProvider === 'inherit') {
    selection.selectedCorrectorModel = '';
  } else if (
    selection.selectedCorrectorModel
    && !isReportedCorrectorModel(
      capabilities,
      selection.selectedCorrectorProvider,
      selection.selectedCorrectorModel,
    )
    && correctorProviderSupportsManual(capabilities, selection.selectedCorrectorProvider)
  ) {
    selection.manualCorrectorModel = true;
    selection.manualCorrectorModelId = selection.selectedCorrectorModel;
  } else if (!selection.selectedCorrectorModel) {
    selection.selectedCorrectorModel = voiceCorrectorModels(
      capabilities, configuration, schema, selection.selectedCorrectorProvider,
    ).find((choice) => choice.available)?.id || '';
  }
  return selection;
}

/** Scope delta persisted for the current selection, preserving unrelated keys of the scope. */
export function voiceConfigurationDelta(
  existingDelta: Record<string, unknown>,
  transportMode: 'batch' | 'streaming',
  selection: Readonly<VoiceConsoleSelection>,
  requiresSecondaryBackend: boolean,
): Record<string, unknown> {
  const existingFlags = valueAtPath(existingDelta, 'feature_flags');
  const delta: Record<string, unknown> = {
    ...existingDelta,
    transport_mode: transportMode,
    recognition_strategy: selection.selectedRecognitionStrategy,
    primary_backend: selection.selectedBackend,
    secondary_backends: requiresSecondaryBackend && selection.selectedSecondaryBackend
      ? [selection.selectedSecondaryBackend]
      : [],
    correction_policy: selection.generativeCorrection ? 'generative_rewrite' : 'deterministic',
    review_policy: selection.generativeCorrection ? 'always' : 'on_disagreement',
    feature_flags: {
      ...(existingFlags && typeof existingFlags === 'object' ? existingFlags as Record<string, unknown> : {}),
      generative_corrector: selection.generativeCorrection,
      voice_fusion: selection.selectedRecognitionStrategy === 'parallel_compare',
    },
  };
  if (selection.generativeCorrection) {
    delta['generative_corrector_provider'] = selection.selectedCorrectorProvider;
    delta['generative_corrector_model'] = selection.selectedCorrectorProvider === 'inherit'
      ? ''
      : selection.manualCorrectorModel
        ? selection.manualCorrectorModelId.trim()
        : selection.selectedCorrectorModel;
  }
  return delta;
}

export function voiceScopeDelta(
  configuration: VoiceConfiguration | null,
  scope: VoiceConfigurationTarget,
  scopeId: string,
): Record<string, unknown> {
  const sources = configuration?.sources;
  if (!sources) return {};
  const entries = Array.isArray(sources) ? sources : Object.values(sources);
  const matching = entries.filter((source) => (
    source.scope === scope && String(source.scope_id || '') === scopeId && source.delta
  ));
  return matching.reduce<Record<string, unknown>>((combined, source) => ({
    ...combined,
    ...structuredClone(source.delta || {}),
    feature_flags: {
      ...(valueAtPath(combined, 'feature_flags') as Record<string, unknown> || {}),
      ...(valueAtPath(source.delta, 'feature_flags') as Record<string, unknown> || {}),
    },
  }), {});
}

export function voiceScopeVersion(
  configuration: VoiceConfiguration | null,
  scope: VoiceConfigurationTarget,
  scopeId: string,
): number | undefined {
  const sources = configuration?.sources;
  if (!sources) return undefined;
  const entries = Array.isArray(sources) ? sources : Object.values(sources);
  const source = [...entries].reverse().find((candidate) => (
    candidate.scope === scope && String(candidate.scope_id || '') === scopeId
  ));
  const version = Number(source?.version);
  return Number.isInteger(version) && version > 0 ? version : undefined;
}

function modelIsAvailable(model: { available?: boolean; status?: string }): boolean {
  if (typeof model.available === 'boolean') return model.available;
  const status = String(model.status || '').toLowerCase();
  if (!status) return true;
  return ['ready', 'available', 'configured', 'loaded'].includes(status);
}

function asrBackendLabel(backendId: string, reportedLabel?: string): string {
  const labels: Record<string, string> = {
    vosk: 'Vosk',
    whisper_cpp: 'whisper.cpp',
    faster_whisper: 'faster-whisper',
    voxtral: 'Voxtral',
  };
  return labels[backendId] || reportedLabel || backendId;
}

function uniqueChoices(choices: VoiceChoice[]): VoiceChoice[] {
  const values = new Map<string, VoiceChoice>();
  for (const choice of choices) {
    if (!choice.id || values.has(choice.id)) continue;
    values.set(choice.id, choice);
  }
  return [...values.values()];
}
