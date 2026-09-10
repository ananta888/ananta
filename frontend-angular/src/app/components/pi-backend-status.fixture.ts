/** Minimal deterministic response for Pi readiness projection and rendered-card tests. */
export function piWorkerResponse(url = 'http://worker:5000') {
  return {
    worker: { url }, private: 'must-not-render', native_execution: {
      schema: 'ananta.pi-worker-readiness.v1', client_id: 'pi', free_class: 'open_source_byok',
      state: 'ready_for_assignment', installed: true, expected_version: '0.85.1', verified_version: '0.85.1',
      native_registered: true, auth_status: 'profile_configured_unverified', inference_verified: false,
      inference_cost: 'provider_dependent', model_selection: 'hub_task_profile', execution_scope: 'hub_native_task',
      requires_hub_assignment: true, global_auto_routing: false,
      capabilities: { headless: true, structured_output: true, tools: false, mcp: false,
        workspace_write: false, session_resume: false },
    },
  };
}
