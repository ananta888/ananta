package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.core.ClientResponse;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;

public final class AnantaStatusViewPart extends AbstractAnantaRuntimeViewPart {
    public AnantaStatusViewPart() {
        super("Ananta Runtime Status");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        ClientResponse health = session.services().apiClient().getHealth();
        ClientResponse capabilities = session.services().apiClient().getCapabilities();
        return RuntimeViewResponseFormatter.block("health", health)
                + "\n\n"
                + RuntimeViewResponseFormatter.block("capabilities", capabilities)
                + "\n\nactive_profile=" + AnantaRuntimeBootstrap.profile().getProfileId()
                + "\nbase_url=" + AnantaRuntimeBootstrap.profile().getBaseUrl();
    }
}
