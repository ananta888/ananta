package io.ananta.eclipse.runtime.platform;

import io.ananta.eclipse.runtime.commands.EclipseCommandRegistry;
import io.ananta.eclipse.runtime.context.EclipseContextCaptureRuntime;
import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.CapabilityGate;
import io.ananta.eclipse.runtime.core.ClientProfile;
import io.ananta.eclipse.runtime.preferences.AnantaPreferenceRuntimeStore;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

public final class AnantaRuntimeBootstrap {
    private static final Object LOCK = new Object();
    private static volatile AnantaRuntimeSession session;
    private static volatile EclipseCommandRegistry commandRegistry;

    private AnantaRuntimeBootstrap() {
    }

    public static AnantaRuntimeSession session() {
        AnantaRuntimeSession existing = session;
        if (existing != null) {
            return existing;
        }
        synchronized (LOCK) {
            if (session == null) {
                rebuild();
            }
            return session;
        }
    }

    public static EclipseCommandRegistry commandRegistry() {
        EclipseCommandRegistry existing = commandRegistry;
        if (existing != null) {
            return existing;
        }
        synchronized (LOCK) {
            if (commandRegistry == null) {
                rebuild();
            }
            return commandRegistry;
        }
    }

    public static void reloadFromPreferences() {
        synchronized (LOCK) {
            rebuild();
        }
    }

    public static ClientProfile profile() {
        return AnantaPreferenceRuntimeStore.loadProfile();
    }

    private static void rebuild() {
        ClientProfile profile = AnantaPreferenceRuntimeStore.loadProfile();
        AnantaApiClient apiClient = new AnantaApiClient(profile);
        CapabilityGate capabilityGate = new CapabilityGate(
                Set.of("goals", "review", "patch", "projects", "approvals", "audit", "repair_step_approval"),
                actionPermissions()
        );
        EclipseContextCaptureRuntime contextCaptureRuntime = new EclipseContextCaptureRuntime();
        AnantaRuntimeServices services = new AnantaRuntimeServices(apiClient, capabilityGate, contextCaptureRuntime);
        session = new AnantaRuntimeSession(services);
        commandRegistry = new EclipseCommandRegistry(apiClient, capabilityGate, contextCaptureRuntime);
    }

    private static Map<String, Boolean> actionPermissions() {
        Map<String, Boolean> permissions = new LinkedHashMap<>();
        permissions.put("io.ananta.eclipse.command.analyze", true);
        permissions.put("io.ananta.eclipse.command.review", true);
        permissions.put("io.ananta.eclipse.command.patch", true);
        permissions.put("io.ananta.eclipse.command.new_project", true);
        permissions.put("io.ananta.eclipse.command.evolve_project", true);
        permissions.put("approval:approve", true);
        permissions.put("approval:reject", true);
        permissions.put("repair:approve_step", true);
        return Map.copyOf(permissions);
    }
}
