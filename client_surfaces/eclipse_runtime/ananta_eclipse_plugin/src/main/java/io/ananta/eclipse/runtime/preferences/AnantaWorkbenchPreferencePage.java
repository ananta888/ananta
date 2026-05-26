package io.ananta.eclipse.runtime.preferences;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientProfile;
import io.ananta.eclipse.runtime.core.ClientResponse;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;
import io.ananta.eclipse.runtime.snake.AnantaSnakePrivacySettings;
import io.ananta.eclipse.runtime.snake.AnantaSnakeUiPreferences;

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
    private Button snakeEnabledCheckbox;
    private Spinner animationFpsSpinner;
    private Spinner followDistanceSpinner;
    private Spinner overlayOpacitySpinner;
    private Button localOnlyModeCheckbox;
    private Button snakeHubEnabledCheckbox;
    private Button allowSelectionContentCheckbox;
    private Button allowFileContentCheckbox;
    private Button allowExternalProvidersCheckbox;
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

        snakeEnabledCheckbox = new Button(root, SWT.CHECK);
        snakeEnabledCheckbox.setText("Snake Enabled by Default");
        snakeEnabledCheckbox.setLayoutData(new GridData(SWT.LEFT, SWT.CENTER, false, false, 3, 1));

        new Label(root, SWT.NONE).setText("Animation FPS");
        animationFpsSpinner = new Spinner(root, SWT.BORDER);
        animationFpsSpinner.setMinimum(15);
        animationFpsSpinner.setMaximum(30);
        animationFpsSpinner.setLayoutData(new GridData(SWT.FILL, SWT.CENTER, true, false));
        new Label(root, SWT.NONE);

        new Label(root, SWT.NONE).setText("Follow Distance (px)");
        followDistanceSpinner = new Spinner(root, SWT.BORDER);
        followDistanceSpinner.setMinimum(4);
        followDistanceSpinner.setMaximum(120);
        followDistanceSpinner.setLayoutData(new GridData(SWT.FILL, SWT.CENTER, true, false));
        new Label(root, SWT.NONE);

        new Label(root, SWT.NONE).setText("Overlay Opacity (%)");
        overlayOpacitySpinner = new Spinner(root, SWT.BORDER);
        overlayOpacitySpinner.setMinimum(10);
        overlayOpacitySpinner.setMaximum(100);
        overlayOpacitySpinner.setLayoutData(new GridData(SWT.FILL, SWT.CENTER, true, false));
        new Label(root, SWT.NONE);

        snakeHubEnabledCheckbox = new Button(root, SWT.CHECK);
        snakeHubEnabledCheckbox.setText("Enable Snake Hub Integration");
        snakeHubEnabledCheckbox.setLayoutData(new GridData(SWT.LEFT, SWT.CENTER, false, false, 3, 1));

        localOnlyModeCheckbox = new Button(root, SWT.CHECK);
        localOnlyModeCheckbox.setText("Force Local-only Mode");
        localOnlyModeCheckbox.setLayoutData(new GridData(SWT.LEFT, SWT.CENTER, false, false, 3, 1));

        allowSelectionContentCheckbox = new Button(root, SWT.CHECK);
        allowSelectionContentCheckbox.setText("Allow Selection Content for Snake Context");
        allowSelectionContentCheckbox.setLayoutData(new GridData(SWT.LEFT, SWT.CENTER, false, false, 3, 1));

        allowFileContentCheckbox = new Button(root, SWT.CHECK);
        allowFileContentCheckbox.setText("Allow Full File Content for Snake Context");
        allowFileContentCheckbox.setLayoutData(new GridData(SWT.LEFT, SWT.CENTER, false, false, 3, 1));

        allowExternalProvidersCheckbox = new Button(root, SWT.CHECK);
        allowExternalProvidersCheckbox.setText("Allow External Providers for IDE Context");
        allowExternalProvidersCheckbox.setLayoutData(new GridData(SWT.LEFT, SWT.CENTER, false, false, 3, 1));

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
        snakeEnabledCheckbox.setSelection(false);
        animationFpsSpinner.setSelection(20);
        followDistanceSpinner.setSelection(24);
        overlayOpacitySpinner.setSelection(60);
        localOnlyModeCheckbox.setSelection(true);
        snakeHubEnabledCheckbox.setSelection(false);
        allowSelectionContentCheckbox.setSelection(false);
        allowFileContentCheckbox.setSelection(false);
        allowExternalProvidersCheckbox.setSelection(false);
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
        AnantaSnakeUiPreferences snakeUiPreferences;
        try {
            snakeUiPreferences = buildSnakeUiPreferences();
        } catch (IllegalArgumentException exc) {
            setErrorMessage(exc.getMessage());
            return false;
        }
        AnantaPreferenceRuntimeStore.saveProfile(profile);
        AnantaPreferenceRuntimeStore.saveSnakeUiPreferences(snakeUiPreferences);
        AnantaPreferenceRuntimeStore.saveSnakeHubEnabled(
                snakeHubEnabledCheckbox.getSelection() && !snakeUiPreferences.localOnlyMode()
        );
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
        AnantaSnakeUiPreferences snakeUiPreferences = AnantaPreferenceRuntimeStore.loadSnakeUiPreferences();
        snakeEnabledCheckbox.setSelection(snakeUiPreferences.snakeEnabledByDefault());
        animationFpsSpinner.setSelection(snakeUiPreferences.animationFps());
        followDistanceSpinner.setSelection(snakeUiPreferences.followDistancePx());
        overlayOpacitySpinner.setSelection(snakeUiPreferences.overlayOpacityPercent());
        localOnlyModeCheckbox.setSelection(snakeUiPreferences.localOnlyMode());
        allowSelectionContentCheckbox.setSelection(snakeUiPreferences.privacySettings().allowSelectionContent());
        allowFileContentCheckbox.setSelection(snakeUiPreferences.privacySettings().allowFileContent());
        allowExternalProvidersCheckbox.setSelection(snakeUiPreferences.privacySettings().allowExternalProviders());
        snakeHubEnabledCheckbox.setSelection(
                AnantaPreferenceRuntimeStore.loadSnakeHubEnabled() && !snakeUiPreferences.localOnlyMode()
        );
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

    private AnantaSnakeUiPreferences buildSnakeUiPreferences() {
        AnantaSnakePrivacySettings privacySettings = new AnantaSnakePrivacySettings(
                allowSelectionContentCheckbox.getSelection(),
                allowFileContentCheckbox.getSelection(),
                allowExternalProvidersCheckbox.getSelection()
        );
        return new AnantaSnakeUiPreferences(
                snakeEnabledCheckbox.getSelection(),
                animationFpsSpinner.getSelection(),
                followDistanceSpinner.getSelection(),
                overlayOpacitySpinner.getSelection(),
                localOnlyModeCheckbox.getSelection(),
                privacySettings
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
