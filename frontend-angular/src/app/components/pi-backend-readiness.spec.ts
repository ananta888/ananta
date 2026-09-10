import { piReadinessUnavailable, piReadinessView } from './pi-backend-readiness';
import { piWorkerResponse } from './pi-backend-status.fixture';

describe('Pi Worker readiness display projection', () => {
  it('describes configuration without claiming successful authentication or inference', () => {
    const response = piWorkerResponse();
    const before = JSON.stringify(response);
    const view = piReadinessView(response, response.worker.url);
    expect(view.label).toBe('Für Hub-Zuweisungen konfiguriert');
    expect(view.tone).toBe('info');
    expect(view.version).toBe('0.85.1');
    expect(view.auth).toContain('nicht geprüft');
    expect(JSON.stringify(view)).not.toContain('must-not-render');
    expect(JSON.stringify(response)).toBe(before);
  });

  it.each([
    ['state', 'ready'], ['installed', 'true'], ['native_registered', 'true'],
    ['schema', 'unknown'], ['expected_version', '0.85.2'], ['verified_version', '0.85.2'],
    ['auth_status', 'authenticated'], ['inference_verified', true], ['inference_cost', 'free'],
    ['global_auto_routing', true], ['requires_hub_assignment', false], ['capabilities', {}],
    ['free_class', 'free'], ['model_selection', 'worker'], ['execution_scope', 'global'],
  ])('does not trust a changed %s claim', (key, value) => {
    const response = piWorkerResponse();
    const changed = { ...response, native_execution: { ...response.native_execution, [key]: value } };
    expect(piReadinessView(changed, response.worker.url)).toEqual(piReadinessUnavailable());
  });

  it.each([null, [], {}, { native_execution: {} }])('fails closed for an absent/older projection', response => {
    expect(piReadinessView(response, 'http://worker:5000')).toEqual(piReadinessUnavailable());
  });

  it('rejects another Worker response', () => {
    expect(piReadinessView(piWorkerResponse(), 'http://other:5000')).toEqual(piReadinessUnavailable());
  });

  it.each([
    [false, false, null, 'not_installed', 'Nicht installiert'],
    [true, true, null, 'version_unverified', 'Version nicht verifiziert'],
    [true, false, '0.85.1', 'native_not_registered', 'Native Pi-Ausführung nicht registriert'],
  ])('distinguishes incomplete configuration: %s/%s/%s', (installed, native, version, state, label) => {
    const response = piWorkerResponse();
    const changed = { ...response, native_execution: { ...response.native_execution,
      installed, native_registered: native, verified_version: version, state,
      auth_status: native ? 'profile_configured_unverified' : 'task_profile_required',
    } };
    expect(piReadinessView(changed, response.worker.url).label).toBe(label);
  });
});
