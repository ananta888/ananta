package io.ananta.eclipse.runtime.core;

import java.util.Map;
import java.util.Objects;
import java.util.Set;

public final class CapabilityGate {
    private final Set<String> capabilities;
    private final Map<String, Boolean> actionPermissions;

    public CapabilityGate(Set<String> capabilities, Map<String, Boolean> actionPermissions) {
        this.capabilities = capabilities == null ? Set.of() : Set.copyOf(capabilities);
        this.actionPermissions = actionPermissions == null ? Map.of() : Map.copyOf(actionPermissions);
    }

    public GateDecision evaluate(String actionId, String requiredCapability) {
        String normalizedAction = Objects.toString(actionId, "").trim();
        if (normalizedAction.isBlank()) {
            return GateDecision.denied("invalid_action");
        }
        if (!actionPermissions.getOrDefault(normalizedAction, false)) {
            return GateDecision.denied("permission_denied");
        }
        String capability = Objects.toString(requiredCapability, "").trim();
        if (!capability.isBlank() && !capabilities.contains(capability)) {
            return GateDecision.denied("capability_missing:" + capability);
        }
        return GateDecision.allowed("allowed");
    }

    public record GateDecision(boolean allowed, String reason) {
        public static GateDecision denied(String reason) {
            return new GateDecision(false, reason);
        }

        public static GateDecision allowed(String reason) {
            return new GateDecision(true, reason);
        }
    }
}
