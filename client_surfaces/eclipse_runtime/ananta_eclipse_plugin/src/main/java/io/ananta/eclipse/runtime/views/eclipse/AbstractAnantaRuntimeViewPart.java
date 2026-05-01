package io.ananta.eclipse.runtime.views.eclipse;

import org.eclipse.swt.SWT;
import org.eclipse.swt.layout.FillLayout;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Label;
import org.eclipse.ui.part.ViewPart;

public abstract class AbstractAnantaRuntimeViewPart extends ViewPart {
    private final String viewTitle;

    protected AbstractAnantaRuntimeViewPart(String viewTitle) {
        this.viewTitle = viewTitle;
    }

    @Override
    public void createPartControl(Composite parent) {
        parent.setLayout(new FillLayout());
        Label label = new Label(parent, SWT.WRAP);
        label.setText(viewTitle + "\n\nAnanta data is loaded through the Hub-owned runtime model.");
    }

    @Override
    public void setFocus() {
    }
}
