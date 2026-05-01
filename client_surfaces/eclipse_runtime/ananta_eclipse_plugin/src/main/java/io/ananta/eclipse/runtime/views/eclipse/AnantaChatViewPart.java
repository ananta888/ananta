package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.chat.ChatRuntimeModel;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;

public final class AnantaChatViewPart extends AbstractAnantaRuntimeViewPart {
    private final ChatRuntimeModel model = new ChatRuntimeModel();

    public AnantaChatViewPart() {
        super("Ananta Chat");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        if (model.messages().isEmpty()) {
            model.addMessage(
                    "system",
                    "Chat is connected to the Hub runtime. Use command \"Ananta Chat\" and follow-up views for task/artifact flows.",
                    null
            );
        }
        return RuntimeViewResponseFormatter.block("health", session.services().apiClient().getHealth())
                + "\n\nmessages=" + model.messages().size()
                + "\nlatest=" + model.messages().get(model.messages().size() - 1).text();
    }
}
