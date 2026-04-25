import { describe, expect, it } from "vitest";
import { buildCapabilitySnapshot, evaluateWorkflowCommand, toCommandContextKey } from "../src/runtime/capabilityGate";
import { ClientResponse } from "../src/runtime/types";

function response(
  data: Record<string, unknown> | null,
  state: ClientResponse<Record<string, unknown>>["state"] = "healthy"
): ClientResponse<Record<string, unknown>> {
  return {
    ok: state === "healthy",
    statusCode: state === "healthy" ? 200 : 422,
    state,
    data,
    error: state === "healthy" ? null : `request_failed:${state}`,
    retriable: false
  };
}

describe("capability gate", () => {
  it("allows command when capability is present", () => {
    const snapshot = buildCapabilitySnapshot(response({ capabilities: ["goals", "review"] }));
    const decision = evaluateWorkflowCommand(snapshot, "ananta.submitGoal");
    expect(decision.allowed).toBe(true);
  });

  it("denies when required capability is missing", () => {
    const snapshot = buildCapabilitySnapshot(response({ capabilities: ["goals"] }));
    const decision = evaluateWorkflowCommand(snapshot, "ananta.patchPlan");
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toContain("capability_missing");
  });

  it("denies when action permission is false", () => {
    const snapshot = buildCapabilitySnapshot(
      response({
        capabilities: ["goals", "review"],
        action_permissions: {
          "ananta.reviewFile": false
        }
      })
    );
    const decision = evaluateWorkflowCommand(snapshot, "ananta.reviewFile");
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toBe("permission_denied");
  });

  it("exposes stable context keys", () => {
    expect(toCommandContextKey("ananta.projectEvolve")).toBe("ananta.capability.projectEvolve");
  });
});
