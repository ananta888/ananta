import {
  VoiceCapabilityStatus,
  VoiceConfiguration,
  VoiceModelCapability,
} from './voice.models';
import { valueAtPath } from './voice-ui.helpers';

export interface VoiceChoice {
  id: string;
  label: string;
  available: boolean;
  reason: string;
}

export function buildCorrectorProviders(
  capabilities: VoiceCapabilityStatus | null,
  configuration: VoiceConfiguration | null,
): VoiceChoice[] {
  const reported = (capabilities?.correction_providers || []).map((provider) => ({
    id: String(provider.id || '').trim().toLowerCase(),
    label: correctorProviderLabel(provider.id, provider.display_name),
    available: provider.available === true,
    reason: String(provider.reason_code || (provider.available === true ? '' : 'voice.corrector.provider_unavailable')),
  })).filter((provider) => provider.id);
  const models = correctionCapabilityModels(capabilities);
  const modelProviderIds = new Set(models.map(correctionModelProvider));
  const modelProviders = [...modelProviderIds].map((id) => {
    const providerModels = models.filter((model) => correctionModelProvider(model) === id);
    const ready = providerModels.find(correctionModelIsAvailable);
    const unavailable = providerModels.find((model) => !correctionModelIsAvailable(model));
    return {
      id,
      label: correctorProviderLabel(id),
      available: Boolean(ready),
      reason: String(ready?.reason_code || unavailable?.reason_code || (ready ? '' : 'voice.corrector.provider_unavailable')),
    };
  });
  const currentProvider = configuredCorrectorProvider(configuration);
  const defaultTarget = capabilities?.correction_default;
  const inheritAvailable = defaultTarget?.available === true
    && Boolean(defaultTarget.provider && defaultTarget.model);
  const inherit: VoiceChoice = {
    id: 'inherit',
    label: 'Allgemeine LLM-Vorgabe',
    available: inheritAvailable,
    reason: inheritAvailable ? '' : 'voice.corrector.default_unavailable',
  };
  const ids = new Set([
    'embedded',
    ...reported.map((provider) => provider.id),
    ...modelProviders.map((provider) => provider.id),
    ...(currentProvider === 'inherit' ? [] : [currentProvider]),
  ]);
  const providers = [...ids].map((id) => {
    const capability = reported.find((provider) => provider.id === id);
    const modelProvider = modelProviders.find((provider) => provider.id === id && provider.available)
      || modelProviders.find((provider) => provider.id === id);
    return capability || modelProvider || {
      id,
      label: correctorProviderLabel(id),
      available: false,
      reason: 'voice.corrector.provider_not_reported',
    };
  });
  const order = new Map([['embedded', 1], ['ollama', 2], ['lmstudio', 3]]);
  providers.sort((left, right) => (
    (order.get(left.id) ?? 10) - (order.get(right.id) ?? 10)
    || left.label.localeCompare(right.label)
  ));
  return [inherit, ...providers];
}

export function buildCorrectorModels(
  capabilities: VoiceCapabilityStatus | null,
  configuration: VoiceConfiguration | null,
  schemaChoices: VoiceChoice[],
  providerId: string,
): VoiceChoice[] {
  const effectiveProvider = providerId === 'inherit'
    ? String(capabilities?.correction_default?.provider || '').trim().toLowerCase()
    : String(providerId || '').trim().toLowerCase();
  const capabilityModels = correctionCapabilityModels(capabilities)
    .filter((model) => correctionModelProvider(model) === effectiveProvider)
    .map((model) => ({
      id: model.id,
      label: `${model.display_name || model.id}${model.revision ? ` · ${model.revision}` : ''}`,
      available: correctionModelIsAvailable(model),
      reason: String(model.reason_code || (correctionModelIsAvailable(model) ? '' : 'voice.corrector.unavailable')),
    }));
  const current = String(valueAtPath(configuration?.effective, 'generative_corrector_model') || '');
  const configuredProvider = configuredCorrectorProvider(configuration);
  const ids = new Set([
    ...capabilityModels.map((choice) => choice.id),
    ...(effectiveProvider === 'embedded' ? schemaChoices.map((choice) => choice.id) : []),
    ...(current && configuredProvider === effectiveProvider ? [current] : []),
  ]);
  return [...ids].map((id) => {
    const capability = capabilityModels.find((choice) => choice.id === id);
    const schema = schemaChoices.find((choice) => choice.id === id);
    return capability || {
      id,
      label: schema?.label || id,
      available: false,
      reason: schema?.reason || 'voice.corrector.not_reported',
    };
  });
}

export function correctorProviderSupportsManual(
  capabilities: VoiceCapabilityStatus | null,
  providerId: string,
): boolean {
  const normalized = String(providerId || '').trim().toLowerCase();
  return capabilities?.correction_providers?.some((provider) => (
    String(provider.id || '').trim().toLowerCase() === normalized
    && provider.supports_manual_model === true
  )) === true;
}

export function correctionDefaultLabel(capabilities: VoiceCapabilityStatus | null): string {
  const target = capabilities?.correction_default;
  if (!target) return 'Der Hub meldet keine allgemeine LLM-Vorgabe.';
  const configured = String(target.configured_model || '').trim();
  const model = configured && configured !== target.model
    ? `${configured} → ${target.model || '–'}`
    : target.model || '–';
  return `${correctorProviderLabel(target.provider)} · ${model} · ${target.source || 'Hub-Konfiguration'}`;
}

export function isReportedCorrectorModel(
  capabilities: VoiceCapabilityStatus | null,
  providerId: string,
  modelId: string,
): boolean {
  return correctionCapabilityModels(capabilities).some((model) => (
    correctionModelProvider(model) === providerId && model.id === modelId
  ));
}

export function isVoiceCorrectionModel(model: VoiceModelCapability): boolean {
  const role = `${model.role || ''} ${model.purpose || ''} ${model.model_type || ''} ${model.backend || ''} ${model.engine || ''}`.toLowerCase();
  const capabilities = (model.capabilities || []).join(' ').toLowerCase();
  return /corrector|rewrite|text.correction|generative/.test(`${role} ${capabilities}`);
}

export function validCorrectorModelId(value: string, providerId = ''): boolean {
  const modelId = String(value || '').trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,191}$/.test(modelId)) return false;
  const provider = String(providerId || '').trim().toLowerCase();
  return !provider || provider === 'embedded' || `${provider}:${modelId}`.length <= 192;
}

function configuredCorrectorProvider(configuration: VoiceConfiguration | null): string {
  return String(
    valueAtPath(configuration?.effective, 'generative_corrector_provider') || 'embedded',
  ).trim().toLowerCase() || 'embedded';
}

function correctionCapabilityModels(capabilities: VoiceCapabilityStatus | null): VoiceModelCapability[] {
  const explicit = capabilities?.correction_models || [];
  if (explicit.length) return explicit;
  return [
    ...(capabilities?.models || []),
    ...(capabilities?.model_catalog || []),
  ].filter(isVoiceCorrectionModel);
}

function correctionModelProvider(model: VoiceModelCapability): string {
  return String(model.provider || 'embedded').trim().toLowerCase() || 'embedded';
}

function correctionModelIsAvailable(model: VoiceModelCapability): boolean {
  if (typeof model.available === 'boolean') return model.available;
  return ['ready', 'available', 'configured', 'loaded'].includes(String(model.status || '').toLowerCase());
}

function correctorProviderLabel(providerId: string, displayName?: string): string {
  const id = String(providerId || '').trim().toLowerCase();
  const explicit = String(displayName || '').trim();
  if (explicit) return explicit;
  if (id === 'embedded') return 'Embedded (lokaler Corrector-Worker)';
  if (id === 'ollama') return 'Ollama';
  if (id === 'lmstudio') return 'LM Studio';
  return providerId || 'Unbekannter Provider';
}
