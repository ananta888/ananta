"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.WORKFLOW_COMMANDS = void 0;
exports.toCommandContextKey = toCommandContextKey;
exports.requiredCapabilityFor = requiredCapabilityFor;
exports.buildCapabilitySnapshot = buildCapabilitySnapshot;
exports.evaluateWorkflowCommand = evaluateWorkflowCommand;
exports.evaluateCapabilityAction = evaluateCapabilityAction;
exports.WORKFLOW_COMMANDS = [
    "ananta.submitGoal",
    "ananta.analyzeSelection",
    "ananta.reviewFile",
    "ananta.patchPlan",
    "ananta.projectNew",
    "ananta.projectEvolve"
];
const GATE_RULES = {
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
function normalizeToken(value) {
    return String(value || "").trim().toLowerCase();
}
function asRecord(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
        return null;
    }
    return value;
}
function readStringArray(value) {
    if (!Array.isArray(value)) {
        return [];
    }
    const out = [];
    for (const candidate of value) {
        if (typeof candidate === "string" && candidate.trim().length > 0) {
            out.push(candidate.trim());
        }
    }
    return out;
}
function parseCapabilities(payload) {
    const capabilities = new Set();
    for (const key of CAPABILITY_KEYS) {
        const values = readStringArray(payload[key]);
        for (const capability of values) {
            capabilities.add(normalizeToken(capability));
        }
    }
    return capabilities;
}
function parsePermissionObject(payload, target) {
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
function parsePermissionLists(payload, target) {
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
function parseActionPermissions(payload) {
    const actionPermissions = new Map();
    parsePermissionObject(payload, actionPermissions);
    parsePermissionLists(payload, actionPermissions);
    return actionPermissions;
}
function resolvePermissionCandidates(snapshot, candidates) {
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
function toCommandContextKey(commandId) {
    return `ananta.capability.${commandId.replace("ananta.", "")}`;
}
function requiredCapabilityFor(commandId) {
    return GATE_RULES[commandId].requiredCapability;
}
function buildCapabilitySnapshot(response) {
    const payload = asRecord(response.data);
    if (!payload) {
        return {
            loaded: false,
            state: response.state,
            statusCode: response.statusCode,
            capabilities: new Set(),
            actionPermissions: new Map()
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
function evaluateWorkflowCommand(snapshot, commandId) {
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
function evaluateCapabilityAction(snapshot, input) {
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
//# sourceMappingURL=capabilityGate.js.map