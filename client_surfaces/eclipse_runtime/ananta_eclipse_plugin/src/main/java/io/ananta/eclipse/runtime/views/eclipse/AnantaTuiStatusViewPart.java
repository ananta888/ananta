package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;
import io.ananta.eclipse.runtime.views.EclipseTuiRuntimeBridge;

import java.util.Map;

public final class AnantaTuiStatusViewPart extends AbstractAnantaRuntimeViewPart {
    public AnantaTuiStatusViewPart() {
        super("Ananta TUI Status");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        EclipseTuiRuntimeBridge.TuiStatusPanel panel = new EclipseTuiRuntimeBridge().buildStatusPanel(
                AnantaRuntimeBootstrap.profile().getProfileId(),
                AnantaRuntimeBootstrap.profile().getBaseUrl(),
                true,
                Map.of("profile", AnantaRuntimeBootstrap.profile().getProfileId())
        );
        return "runtime_available=" + panel.runtimeAvailable()
                + "\nlaunch_command=" + panel.launchCommand()
                + "\nprofile_id=" + panel.profileId()
                + "\nendpoint=" + panel.endpoint()
                + "\nhandoff_context=" + panel.handoffContext();
    }
}
