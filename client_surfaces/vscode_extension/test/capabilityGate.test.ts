import { describe, expect, it } from "vitest";
import {
  buildCapabilitySnapshot,
  evaluateCapabilityAction,
  evaluateWorkflowCommand,
  toCommandContextKey
} from "../src/runtime/capabilityGate";
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

  it("evaluates generic approval actions with capability and permissions", () => {
    const snapshot = buildCapabilitySnapshot(
      response({
        capabilities: ["approvals"],
        action_permissions: {
          "ananta.approveApproval": true,
          "ananta.rejectApproval": false
        }
      })
    );
    const approve = evaluateCapabilityAction(snapshot, {
      actionId: "ananta.approveApproval",
      requiredCapability: "approvals"
    });
    const reject = evaluateCapabilityAction(snapshot, {
      actionId: "ananta.rejectApproval",
      requiredCapability: "approvals"
    });
    expect(approve.allowed).toBe(true);
    expect(reject.allowed).toBe(false);
    expect(reject.reason).toBe("permission_denied");
  });

  it("denies commands when capability response is degraded or malformed", () => {
    const malformed = buildCapabilitySnapshot(response(null, "malformed_response"));
    const denied = evaluateWorkflowCommand(malformed, "ananta.submitGoal");
    expect(denied.allowed).toBe(false);
    expect(denied.reason).toBe("capability_probe_malformed_response");
  });

  it("denies commands when backend reports policy denial", () => {
    const deniedSnapshot = buildCapabilitySnapshot(response({ capabilities: ["goals"] }, "policy_denied"));
    const denied = evaluateWorkflowCommand(deniedSnapshot, "ananta.submitGoal");
    expect(denied.allowed).toBe(false);
    expect(denied.reason).toBe("capability_probe_policy_denied");
  });
});
