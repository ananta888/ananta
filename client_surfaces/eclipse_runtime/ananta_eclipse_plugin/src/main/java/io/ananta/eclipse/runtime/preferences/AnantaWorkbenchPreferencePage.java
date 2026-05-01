package io.ananta.eclipse.runtime.preferences;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientProfile;
import io.ananta.eclipse.runtime.core.ClientResponse;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;

import org.eclipse.jface.dialogs.MessageDialog;
import org.eclipse.jface.preference.PreferencePage;
import org.eclipse.swt.SWT;
import org.eclipse.swt.layout.GridData;
import org.eclipse.swt.layout.GridLayout;
import org.eclipse.swt.widgets.Button;
import org.eclipse.swt.widgets.Combo;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Control;
import org.eclipse.swt.widgets.Label;
import org.eclipse.swt.widgets.Spinner;
import org.eclipse.swt.widgets.Text;
import org.eclipse.ui.IWorkbench;
import org.eclipse.ui.IWorkbenchPreferencePage;

public final class AnantaWorkbenchPreferencePage extends PreferencePage implements IWorkbenchPreferencePage {
    private Text profileIdText;
    private Text baseUrlText;
    private Combo authModeCombo;
    private Text environmentText;
    private Text tokenText;
    private Spinner timeoutSpinner;
    private Label connectionStatusLabel;

    @Override
    public void init(IWorkbench workbench) {
    }

    @Override
    protected Control createContents(Composite parent) {
        Composite root = new Composite(parent, SWT.NONE);
        root.setLayout(new GridLayout(3, false));

        profileIdText = createTextField(root, "Profile ID");
        baseUrlText = createTextField(root, "Hub Base URL");

        new Label(root, SWT.NONE).setText("Auth Mode");
        authModeCombo = new Combo(root, SWT.DROP_DOWN | SWT.READ_ONLY);
        authModeCombo.setItems("session_token", "none");
        authModeCombo.setLayoutData(new GridData(SWT.FILL, SWT.CENTER, true, false));
        new Label(root, SWT.NONE);

        environmentText = createTextField(root, "Environment");
        tokenText = createTextField(root, "Token (secure storage)");
        tokenText.setEchoChar('*');

        new Label(root, SWT.NONE).setText("Timeout (seconds)");
        timeoutSpinner = new Spinner(root, SWT.BORDER);
        timeoutSpinner.setMinimum(1);
        timeoutSpinner.setMaximum(60);
        timeoutSpinner.setLayoutData(new GridData(SWT.FILL, SWT.CENTER, true, false));
        new Label(root, SWT.NONE);

        Button testConnection = new Button(root, SWT.PUSH);
        testConnection.setText("Test Connection");
        testConnection.setLayoutData(new GridData(SWT.LEFT, SWT.CENTER, false, false, 3, 1));
        testConnection.addListener(SWT.Selection, event -> testConnection());

        connectionStatusLabel = new Label(root, SWT.WRAP);
        connectionStatusLabel.setLayoutData(new GridData(SWT.FILL, SWT.TOP, true, false, 3, 1));
        connectionStatusLabel.setText("");

        loadProfile();
        return root;
    }

    @Override
    protected void performDefaults() {
        profileIdText.setText("default");
        baseUrlText.setText("http://localhost:8080");
        authModeCombo.setText("session_token");
        environmentText.setText("local");
        tokenText.setText("");
        timeoutSpinner.setSelection(15);
        connectionStatusLabel.setText("");
        super.performDefaults();
    }

    @Override
    public boolean performOk() {
        AnantaPreferencePage.ProfilePreferenceDraft draft = buildDraft();
        AnantaPreferencePage.PreferenceValidation validation = new AnantaPreferencePage().validate(draft);
        if (!validation.valid()) {
            setErrorMessage(validation.error());
            return false;
        }
        setErrorMessage(null);
        ClientProfile profile = draft.toProfile();
        AnantaPreferenceRuntimeStore.saveProfile(profile);
        AnantaRuntimeBootstrap.reloadFromPreferences();
        setMessage("Ananta profile saved.");
        return true;
    }

    private void loadProfile() {
        ClientProfile profile = AnantaPreferenceRuntimeStore.loadProfile();
        profileIdText.setText(profile.getProfileId());
        baseUrlText.setText(profile.getBaseUrl());
        authModeCombo.setText(profile.getAuthMode());
        environmentText.setText(profile.getEnvironment());
        tokenText.setText(profile.getAuthToken());
        timeoutSpinner.setSelection(profile.getTimeoutSeconds());
    }

    private AnantaPreferencePage.ProfilePreferenceDraft buildDraft() {
        return new AnantaPreferencePage.ProfilePreferenceDraft(
                profileIdText.getText(),
                baseUrlText.getText(),
                authModeCombo.getText(),
                environmentText.getText(),
                tokenText.getText(),
                timeoutSpinner.getSelection()
        );
    }

    private void testConnection() {
        AnantaPreferencePage.ProfilePreferenceDraft draft = buildDraft();
        AnantaPreferencePage.PreferenceValidation validation = new AnantaPreferencePage().validate(draft);
        if (!validation.valid()) {
            MessageDialog.openError(getShell(), "Ananta Connection", validation.error());
            return;
        }
        ClientProfile profile = draft.toProfile();
        AnantaApiClient api = new AnantaApiClient(profile);
        ClientResponse health = api.getHealth();
        ClientResponse capabilities = api.getCapabilities();
        connectionStatusLabel.setText(
                "Health: " + health.getState().name().toLowerCase()
                        + " (status=" + health.getStatusCode() + ")"
                        + "\nCapabilities: " + capabilities.getState().name().toLowerCase()
                        + " (status=" + capabilities.getStatusCode() + ")"
        );
    }

    private static Text createTextField(Composite parent, String label) {
        new Label(parent, SWT.NONE).setText(label);
        Text text = new Text(parent, SWT.BORDER);
        text.setLayoutData(new GridData(SWT.FILL, SWT.CENTER, true, false));
        new Label(parent, SWT.NONE);
        return text;
    }
}
