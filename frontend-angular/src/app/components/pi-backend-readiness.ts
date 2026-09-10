/** Closed display projection; Worker observations never authorize execution. */
export interface PiReadinessView {
  label: string;
  tone: 'info' | 'warning' | 'unknown';
  version: string;
  auth: string;
}

export const PI_EXPECTED_VERSION = '0.85.1';

export function piReadinessUnavailable(label = 'Status nicht verifiziert'): PiReadinessView {
  return { label, tone: 'unknown', version: 'Nicht verifiziert', auth: 'Nicht verifiziert' };
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export function piReadinessView(response: unknown, workerUrl: string): PiReadinessView {
  const result = record(response);
  const value = record(result['native_execution']);
  const caps = record(value['capabilities']);
  if (
    record(result['worker'])['url'] !== workerUrl || !workerUrl ||
    value['schema'] !== 'ananta.pi-worker-readiness.v1' || value['client_id'] !== 'pi' ||
    value['free_class'] !== 'open_source_byok' || value['expected_version'] !== PI_EXPECTED_VERSION ||
    value['inference_verified'] !== false || value['inference_cost'] !== 'provider_dependent' ||
    value['model_selection'] !== 'hub_task_profile' || value['execution_scope'] !== 'hub_native_task' ||
    value['requires_hub_assignment'] !== true || value['global_auto_routing'] !== false ||
    typeof value['installed'] !== 'boolean' || typeof value['native_registered'] !== 'boolean' ||
    caps['headless'] !== true || caps['structured_output'] !== true || caps['tools'] !== false ||
    caps['mcp'] !== false || caps['workspace_write'] !== false || caps['session_resume'] !== false
  ) return piReadinessUnavailable();

  const installed = value['installed'];
  const native = value['native_registered'];
  const verified = value['verified_version'] === PI_EXPECTED_VERSION;
  const expectedAuth = native ? 'profile_configured_unverified' : 'task_profile_required';
  const expectedState = !installed ? 'not_installed' : !verified ? 'version_unverified'
    : !native ? 'native_not_registered' : 'ready_for_assignment';
  if (
    value['auth_status'] !== expectedAuth || value['state'] !== expectedState ||
    (!installed && verified) || (!verified && value['verified_version'] !== null)
  ) return piReadinessUnavailable();

  const labels: Record<string, string> = {
    not_installed: 'Nicht installiert', version_unverified: 'Version nicht verifiziert',
    native_not_registered: 'Native Pi-Ausführung nicht registriert',
    ready_for_assignment: 'Für Hub-Zuweisungen konfiguriert',
  };
  return {
    label: labels[expectedState], tone: expectedState === 'ready_for_assignment' ? 'info' : 'warning',
    version: verified ? PI_EXPECTED_VERSION : 'Nicht verifiziert',
    auth: native ? 'Profil konfiguriert; Anbieter-Anmeldung nicht geprüft' : 'Hub-Auftragsprofil erforderlich',
  };
}
