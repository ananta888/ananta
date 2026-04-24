import * as vscode from "vscode";
import { AnantaBackendClient } from "./runtime/backendClient";
import { redactSensitiveText, sanitizeErrorMessage } from "./runtime/redaction";
import { AnantaSecretStore } from "./runtime/secretStore";
import { resolveRuntimeSettings } from "./runtime/settings";
import { AnantaStatusTreeProvider } from "./views/statusTreeProvider";

const COMMANDS = {
  checkHealth: "ananta.checkHealth",
  configureProfile: "ananta.configureProfile",
  storeToken: "ananta.storeToken",
  clearToken: "ananta.clearToken"
} as const;

async function buildRuntimeClient(
  context: vscode.ExtensionContext,
  statusView: AnantaStatusTreeProvider,
  output: vscode.OutputChannel
): Promise<{ client: AnantaBackendClient; endpoint: string; profileId: string } | null> {
  const config = vscode.workspace.getConfiguration("ananta");
  const secretStore = new AnantaSecretStore(context.secrets);
  const resolved = await resolveRuntimeSettings(config, secretStore);
  if (!resolved.settings) {
    const message = `Ananta settings invalid: ${resolved.validationErrors.join(", ")}`;
    statusView.setSnapshot({
      connectionState: "invalid_config",
      capabilitiesState: "unknown",
      endpoint: String(config.get("baseUrl", "-")),
      profileId: String(config.get("profileId", "-")),
      details: resolved.validationErrors
    });
    output.appendLine(`[runtime] ${redactSensitiveText(message)}`);
    void vscode.window.showWarningMessage(message);
    return null;
  }

  statusView.setSnapshot({
    connectionState: "configured",
    capabilitiesState: "unknown",
    endpoint: resolved.settings.baseUrl,
    profileId: resolved.settings.profileId,
    details: []
  });
  return {
    client: new AnantaBackendClient(resolved.settings),
    endpoint: resolved.settings.baseUrl,
    profileId: resolved.settings.profileId
  };
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const output = vscode.window.createOutputChannel("Ananta");
  const statusView = new AnantaStatusTreeProvider();
  context.subscriptions.push(output);
  context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.statusView", statusView));

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.configureProfile, async () => {
      await vscode.commands.executeCommand("workbench.action.openSettings", "ananta.");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.storeToken, async () => {
      const value = await vscode.window.showInputBox({
        title: "Store Ananta Auth Token",
        prompt: "Enter token for current Ananta profile",
        password: true,
        ignoreFocusOut: true
      });
      if (!value) {
        return;
      }
      const config = vscode.workspace.getConfiguration("ananta");
      const key = String(config.get("auth.secretStorageKey", "ananta.auth.token"));
      const secretStore = new AnantaSecretStore(context.secrets);
      await secretStore.storeToken(value, key);
      output.appendLine(`[auth] token stored with key=${key}`);
      void vscode.window.showInformationMessage("Ananta token stored in SecretStorage.");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.clearToken, async () => {
      const config = vscode.workspace.getConfiguration("ananta");
      const key = String(config.get("auth.secretStorageKey", "ananta.auth.token"));
      const secretStore = new AnantaSecretStore(context.secrets);
      await secretStore.clearToken(key);
      output.appendLine(`[auth] token cleared with key=${key}`);
      void vscode.window.showInformationMessage("Ananta token removed from SecretStorage.");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.checkHealth, async () => {
      const runtime = await buildRuntimeClient(context, statusView, output);
      if (!runtime) {
        return;
      }
      const { client, endpoint, profileId } = runtime;

      try {
        const health = await client.getHealth();
        const capabilities = await client.getCapabilities();
        const capabilityCount =
          Array.isArray((capabilities.data as Record<string, unknown> | null)?.capabilities) &&
          (capabilities.data as Record<string, unknown>).capabilities
            ? ((capabilities.data as Record<string, unknown>).capabilities as unknown[]).length
            : 0;

        statusView.setSnapshot({
          connectionState: health.state,
          capabilitiesState: capabilities.state,
          endpoint,
          profileId,
          details: [`capability_count=${capabilityCount}`, `health_status=${health.statusCode ?? "none"}`]
        });

        output.appendLine(
          `[health] state=${health.state} status=${health.statusCode ?? "none"} capabilities_state=${capabilities.state}`
        );
        if (health.ok && capabilities.ok) {
          void vscode.window.showInformationMessage("Ananta backend is healthy and capabilities were loaded.");
        } else {
          void vscode.window.showWarningMessage(
            `Ananta degraded: health=${health.state}, capabilities=${capabilities.state}`
          );
        }
      } catch (error) {
        const safeError = sanitizeErrorMessage(error);
        output.appendLine(`[health] failed=${safeError}`);
        statusView.setSnapshot({
          connectionState: "backend_unreachable",
          capabilitiesState: "unknown",
          endpoint,
          profileId,
          details: [safeError]
        });
        void vscode.window.showErrorMessage(`Ananta check failed: ${safeError}`);
      }
    })
  );
}

export function deactivate(): void {
  // no-op
}
