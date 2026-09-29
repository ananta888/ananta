import { normalizeResearchBackendConfigValue } from './settings-config.helpers';

/**
 * Pure projections of the hub's research-backend preflight and evolution
 * provider status payloads into the lists and warnings the settings page shows.
 */

export function listSupportedResearchProvidersValue(researchBackendStatus: any): string[] {
  const providers = researchBackendStatus?.research_backends;
  if (providers && typeof providers === 'object') {
    const names = Object.keys(providers).filter((entry) => !!String(entry || '').trim());
    if (names.length) return names;
  }
  return ['deerflow', 'ananta_research'];
}

export function listResearchBackendPreflightEntriesValue(researchBackendStatus: any): any[] {
  const providers = researchBackendStatus?.research_backends;
  if (!providers || typeof providers !== 'object') return [];
  return Object.values(providers) as any[];
}

export function collectResearchBackendWarnings(config: any, researchBackendStatus: any): string[] {
  const warnings: string[] = [];
  const current = normalizeResearchBackendConfigValue(config?.research_backend);
  const selected = (researchBackendStatus?.research_backends || {})?.[current.provider] || null;
  if (current.enabled && !String(current.command || '').trim()) {
    warnings.push(`Research-Backend ${current.provider} ist aktiviert, aber ohne command konfiguriert.`);
  }
  if (current.enabled && selected && selected.binary_available === false) {
    warnings.push(`Research-Backend ${current.provider} ist aktiviert, aber das konfigurierte Binary ist aktuell nicht verfuegbar.`);
  }
  if (current.enabled && selected && selected.working_dir && selected.working_dir_exists === false) {
    warnings.push(`Research-Backend ${current.provider} verwendet ein fehlendes working_dir: ${selected.working_dir}`);
  }
  return warnings;
}

export function listEvolutionProvidersValue(evolutionProviderStatus: any): any[] {
  const providers = evolutionProviderStatus?.providers;
  return Array.isArray(providers) ? providers : [];
}

export function resolveEvolutionConfigValue(evolutionProviderStatus: any): any {
  const cfg = evolutionProviderStatus?.config;
  return cfg && typeof cfg === 'object'
    ? cfg
    : {
        enabled: false,
        analyze_only: true,
        validate_allowed: false,
        apply_allowed: false,
        require_review_before_apply: true,
      };
}

export function summarizeEvolutionModeValue(cfg: any): string {
  if (!cfg.enabled) return 'disabled';
  if (cfg.analyze_only) return 'analyze_only';
  if (!cfg.apply_allowed) return 'proposal_review';
  return 'controlled_apply';
}

export function collectEvolutionWarnings(cfg: any, providers: any[]): string[] {
  const warnings: string[] = [];
  if (!cfg.enabled) {
    warnings.push('Evolution ist global deaktiviert.');
    return warnings;
  }
  if (cfg.apply_allowed === true && cfg.require_review_before_apply !== true) {
    warnings.push('Apply ist freigegeben, aber Review vor Apply ist nicht erzwungen.');
  }
  if (cfg.apply_allowed === true && cfg.analyze_only === true) {
    warnings.push('Apply ist global freigegeben, aber Provider koennen weiter analyze-only fail-closed bleiben.');
  }
  if (cfg.validate_allowed !== true) {
    warnings.push('Validation ist aktuell nicht global freigegeben.');
  }
  for (const provider of providers) {
    const apply = provider?.capability_matrix?.apply;
    const validate = provider?.capability_matrix?.validate;
    if (apply?.supported && !apply?.available && apply?.fail_closed_reason) {
      warnings.push(`Provider ${provider.provider_name} blockiert Apply: ${apply.fail_closed_reason}`);
    }
    if (validate?.supported && !validate?.available && validate?.fail_closed_reason) {
      warnings.push(`Provider ${provider.provider_name} blockiert Validate: ${validate.fail_closed_reason}`);
    }
  }
  return warnings;
}
