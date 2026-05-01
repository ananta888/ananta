package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;
import io.ananta.eclipse.runtime.platform.RuntimeUiState;

import org.eclipse.swt.SWT;
import org.eclipse.swt.layout.GridData;
import org.eclipse.swt.layout.GridLayout;
import org.eclipse.swt.widgets.Button;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Label;
import org.eclipse.swt.widgets.Text;
import org.eclipse.ui.part.ViewPart;

public abstract class AbstractAnantaRuntimeViewPart extends ViewPart {
    private final String viewTitle;
    private Text outputText;
    private Button refreshButton;

    protected AbstractAnantaRuntimeViewPart(String viewTitle) {
        this.viewTitle = viewTitle;
    }

    @Override
    public void createPartControl(Composite parent) {
        parent.setLayout(new GridLayout(1, false));

        Label title = new Label(parent, SWT.WRAP);
        title.setText(viewTitle);
        title.setLayoutData(new GridData(SWT.FILL, SWT.TOP, true, false));

        refreshButton = new Button(parent, SWT.PUSH);
        refreshButton.setText("Refresh");
        refreshButton.setLayoutData(new GridData(SWT.LEFT, SWT.TOP, false, false));
        refreshButton.addListener(SWT.Selection, event -> refreshView());

        outputText = new Text(parent, SWT.BORDER | SWT.MULTI | SWT.READ_ONLY | SWT.V_SCROLL | SWT.H_SCROLL);
        outputText.setLayoutData(new GridData(SWT.FILL, SWT.FILL, true, true));
        refreshView();
    }

    @Override
    public void setFocus() {
        if (refreshButton != null && !refreshButton.isDisposed()) {
            refreshButton.setFocus();
        }
    }

    protected abstract String renderContent(AnantaRuntimeSession session);

    private void refreshView() {
        AnantaRuntimeSession session = AnantaRuntimeBootstrap.session();
        RuntimeUiState state = session.refreshHealth();
        String stateHeader = "runtime_state=" + state.getState().name().toLowerCase()
                + ", actions_enabled=" + state.isActionsEnabled()
                + ", retry_visible=" + state.isRetryVisible()
                + ", message=" + state.getMessage();
        String content = renderContent(session);
        outputText.setText(stateHeader + "\n\n" + content);
    }
}
