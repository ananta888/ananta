# Usage

1. Verify health and capabilities from `/api/client-surfaces/blender/health` and `/api/client-surfaces/blender/capabilities`.
2. Capture and preview bounded scene context before sending it to the hub.
3. Submit a goal with the approved context and a Blender capability ID.
4. Refresh tasks, artifacts and approvals from the hub.
5. Use export/render/mutation planning first; mutating execution requires explicit approval.
6. Inspect audit/events to distinguish local addon failures, hub policy denial and delegated success.
