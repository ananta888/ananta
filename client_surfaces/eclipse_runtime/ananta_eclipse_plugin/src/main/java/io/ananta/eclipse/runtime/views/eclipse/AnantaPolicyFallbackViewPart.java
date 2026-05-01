package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;
import io.ananta.eclipse.runtime.views.EclipsePolicyFallbackUx;

public final class AnantaPolicyFallbackViewPart extends AbstractAnantaRuntimeViewPart {
    public AnantaPolicyFallbackViewPart() {
        super("Ananta Policy and Browser Fallback");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        EclipsePolicyFallbackUx.PolicyDeniedModel model = new EclipsePolicyFallbackUx().buildPolicyDenied(
                "io.ananta.eclipse.command.analyze",
                "permission_denied",
                "",
                AnantaRuntimeBootstrap.profile().getBaseUrl()
        );
        return "action_id=" + model.actionId()
                + "\ndenial_reason=" + model.denialReason()
                + "\ntrace=" + model.traceId()
                + "\nlinks=" + model.browserFallbackLinks()
                + "\nnext_steps=" + model.nextSteps();
    }
}
