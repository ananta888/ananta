package io.ananta.eclipse.runtime.security;

import io.ananta.eclipse.runtime.core.CapabilityGate;

import java.util.Map;
import java.util.Objects;

public final class ActionPolicyRuntime {
    private final CapabilityGate capabilityGate;
    private final Map<String, String> capabilityByAction;

    public ActionPolicyRuntime(CapabilityGate capabilityGate, Map<String, String> capabilityByAction) {
        this.capabilityGate = Objects.requireNonNull(capabilityGate, "capabilityGate");
        this.capabilityByAction = capabilityByAction == null ? Map.of() : Map.copyOf(capabilityByAction);
    }

    public CapabilityGate.GateDecision evaluate(String actionId) {
        String action = Objects.toString(actionId, "").trim();
        if (!capabilityByAction.containsKey(action)) {
            return CapabilityGate.GateDecision.denied("unknown_action");
        }
        return capabilityGate.evaluate(action, capabilityByAction.get(action));
    }
}
