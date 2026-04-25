import { ClientResponse, DegradedState } from "./types";

export const WORKFLOW_COMMANDS = [
  "ananta.submitGoal",
  "ananta.analyzeSelection",
  "ananta.reviewFile",
  "ananta.patchPlan",
  "ananta.projectNew",
  "ananta.projectEvolve"
] as const;

export type WorkflowCommandId = (typeof WORKFLOW_COMMANDS)[number];

interface GateRule {
  requiredCapability: string;
  actionAliases: string[];
}

const GATE_RULES: Record<WorkflowCommandId, GateRule> = {
  "ananta.submitGoal": {
    requiredCapability: "goals",
    actionAliases: ["goals", "submit_goal", "goal_submit", "goals.submit"]
  },
  "ananta.analyzeSelection": {
    requiredCapability: "goals",
    actionAliases: ["tasks.analyze", "analyze", "analyze_selection"]
  },
  "ananta.reviewFile": {
    requiredCapability: "review",
    actionAliases: ["tasks.review", "review", "review_file"]
  },
  "ananta.patchPlan": {
    requiredCapability: "patch",
    actionAliases: ["tasks.patch-plan", "patch_plan", "patch"]
  },
  "ananta.projectNew": {
    requiredCapability: "projects",
    actionAliases: ["projects.new", "project_new", "new_project"]
  },
  "ananta.projectEvolve": {
    requiredCapability: "projects",
    actionAliases: ["projects.evolve", "project_evolve", "evolve_project"]
  }
};

const CAPABILITY_KEYS = ["capabilities", "enabled_capabilities", "available_capabilities"];
const PERMISSION_KEYS = ["action_permissions", "permissions"];
const ALLOWED_ACTIONS_KEYS = ["allowed_actions", "allowedActions"];
const DENIED_ACTIONS_KEYS = ["denied_actions", "deniedActions"];

export interface CapabilitySnapshot {
  loaded: boolean;
  state: DegradedState;
  statusCode: number | null;
  capabilities: Set<string>;
  actionPermissions: Map<string, boolean>;
}

export interface CommandGateDecision {
  allowed: boolean;
  reason: string;
  requiredCapability: string;
}

export interface CapabilityActionGateInput {
  actionId: string;
  requiredCapability: string;
  actionAliases?: string[];
}

function normalizeToken(value: string): string {
  return String(value || "").trim().toLowerCase();
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const out: string[] = [];
  for (const candidate of value) {
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      out.push(candidate.trim());
    }
  }
  return out;
}

function parseCapabilities(payload: Record<string, unknown>): Set<string> {
  const capabilities = new Set<string>();
  for (const key of CAPABILITY_KEYS) {
    const values = readStringArray(payload[key]);
    for (const capability of values) {
      capabilities.add(normalizeToken(capability));
    }
  }
  return capabilities;
}

function parsePermissionObject(payload: Record<string, unknown>, target: Map<string, boolean>): void {
  for (const key of PERMISSION_KEYS) {
    const container = asRecord(payload[key]);
    if (!container) {
      continue;
    }
    for (const [actionId, allowedRaw] of Object.entries(container)) {
      if (typeof allowedRaw !== "boolean") {
        continue;
      }
      target.set(normalizeToken(actionId), allowedRaw);
    }
  }
}

function parsePermissionLists(payload: Record<string, unknown>, target: Map<string, boolean>): void {
  for (const key of ALLOWED_ACTIONS_KEYS) {
    for (const actionId of readStringArray(payload[key])) {
      target.set(normalizeToken(actionId), true);
    }
  }
  for (const key of DENIED_ACTIONS_KEYS) {
    for (const actionId of readStringArray(payload[key])) {
      target.set(normalizeToken(actionId), false);
    }
  }
}

function parseActionPermissions(payload: Record<string, unknown>): Map<string, boolean> {
  const actionPermissions = new Map<string, boolean>();
  parsePermissionObject(payload, actionPermissions);
  parsePermissionLists(payload, actionPermissions);
  return actionPermissions;
}

function resolvePermissionCandidates(snapshot: CapabilitySnapshot, candidates: string[]): boolean {
  if (snapshot.actionPermissions.size === 0) {
    return true;
  }
  let sawExplicitPermission = false;
  for (const candidate of candidates.map((value) => normalizeToken(value))) {
    if (!snapshot.actionPermissions.has(candidate)) {
      continue;
    }
    sawExplicitPermission = true;
    if (snapshot.actionPermissions.get(candidate) === true) {
      return true;
    }
  }
  return sawExplicitPermission ? false : true;
}

export function toCommandContextKey(commandId: WorkflowCommandId): string {
  return `ananta.capability.${commandId.replace("ananta.", "")}`;
}

export function requiredCapabilityFor(commandId: WorkflowCommandId): string {
  return GATE_RULES[commandId].requiredCapability;
}

export function buildCapabilitySnapshot(response: ClientResponse<Record<string, unknown>>): CapabilitySnapshot {
  const payload = asRecord(response.data);
  if (!payload) {
    return {
      loaded: false,
      state: response.state,
      statusCode: response.statusCode,
      capabilities: new Set<string>(),
      actionPermissions: new Map<string, boolean>()
    };
  }
  return {
    loaded: true,
    state: response.state,
    statusCode: response.statusCode,
    capabilities: parseCapabilities(payload),
    actionPermissions: parseActionPermissions(payload)
  };
}

export function evaluateWorkflowCommand(snapshot: CapabilitySnapshot, commandId: WorkflowCommandId): CommandGateDecision {
  const requiredCapability = requiredCapabilityFor(commandId);
  if (!snapshot.loaded || snapshot.state !== "healthy") {
    return {
      allowed: false,
      reason: `capability_probe_${snapshot.state}`,
      requiredCapability
    };
  }

  if (!snapshot.capabilities.has(normalizeToken(requiredCapability))) {
    return {
      allowed: false,
      reason: `capability_missing:${requiredCapability}`,
      requiredCapability
    };
  }

  const rule = GATE_RULES[commandId];
  if (!resolvePermissionCandidates(snapshot, [commandId, ...rule.actionAliases])) {
    return {
      allowed: false,
      reason: "permission_denied",
      requiredCapability
    };
  }

  return {
    allowed: true,
    reason: "allowed",
    requiredCapability
  };
}

export function evaluateCapabilityAction(
  snapshot: CapabilitySnapshot,
  input: CapabilityActionGateInput
): CommandGateDecision {
  const requiredCapability = String(input.requiredCapability || "").trim();
  if (!snapshot.loaded || snapshot.state !== "healthy") {
    return {
      allowed: false,
      reason: `capability_probe_${snapshot.state}`,
      requiredCapability
    };
  }

  if (requiredCapability.length > 0 && !snapshot.capabilities.has(normalizeToken(requiredCapability))) {
    return {
      allowed: false,
      reason: `capability_missing:${requiredCapability}`,
      requiredCapability
    };
  }

  const actionId = String(input.actionId || "").trim();
  const aliases = Array.isArray(input.actionAliases) ? input.actionAliases : [];
  if (actionId.length > 0 && !resolvePermissionCandidates(snapshot, [actionId, ...aliases])) {
    return {
      allowed: false,
      reason: "permission_denied",
      requiredCapability
    };
  }

  return {
    allowed: true,
    reason: "allowed",
    requiredCapability
  };
}
