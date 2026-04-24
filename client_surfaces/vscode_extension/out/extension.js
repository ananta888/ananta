"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const backendClient_1 = require("./runtime/backendClient");
const redaction_1 = require("./runtime/redaction");
const secretStore_1 = require("./runtime/secretStore");
const settings_1 = require("./runtime/settings");
const statusTreeProvider_1 = require("./views/statusTreeProvider");
const COMMANDS = {
    checkHealth: "ananta.checkHealth",
    configureProfile: "ananta.configureProfile",
    storeToken: "ananta.storeToken",
    clearToken: "ananta.clearToken"
};
async function buildRuntimeClient(context, statusView, output) {
    const config = vscode.workspace.getConfiguration("ananta");
    const secretStore = new secretStore_1.AnantaSecretStore(context.secrets);
    const resolved = await (0, settings_1.resolveRuntimeSettings)(config, secretStore);
    if (!resolved.settings) {
        const message = `Ananta settings invalid: ${resolved.validationErrors.join(", ")}`;
        statusView.setSnapshot({
            connectionState: "invalid_config",
            capabilitiesState: "unknown",
            endpoint: String(config.get("baseUrl", "-")),
            profileId: String(config.get("profileId", "-")),
            details: resolved.validationErrors
        });
        output.appendLine(`[runtime] ${(0, redaction_1.redactSensitiveText)(message)}`);
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
        client: new backendClient_1.AnantaBackendClient(resolved.settings),
        endpoint: resolved.settings.baseUrl,
        profileId: resolved.settings.profileId
    };
}
async function activate(context) {
    const output = vscode.window.createOutputChannel("Ananta");
    const statusView = new statusTreeProvider_1.AnantaStatusTreeProvider();
    context.subscriptions.push(output);
    context.subscriptions.push(vscode.window.registerTreeDataProvider("ananta.statusView", statusView));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.configureProfile, async () => {
        await vscode.commands.executeCommand("workbench.action.openSettings", "ananta.");
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.storeToken, async () => {
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
        const secretStore = new secretStore_1.AnantaSecretStore(context.secrets);
        await secretStore.storeToken(value, key);
        output.appendLine(`[auth] token stored with key=${key}`);
        void vscode.window.showInformationMessage("Ananta token stored in SecretStorage.");
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.clearToken, async () => {
        const config = vscode.workspace.getConfiguration("ananta");
        const key = String(config.get("auth.secretStorageKey", "ananta.auth.token"));
        const secretStore = new secretStore_1.AnantaSecretStore(context.secrets);
        await secretStore.clearToken(key);
        output.appendLine(`[auth] token cleared with key=${key}`);
        void vscode.window.showInformationMessage("Ananta token removed from SecretStorage.");
    }));
    context.subscriptions.push(vscode.commands.registerCommand(COMMANDS.checkHealth, async () => {
        const runtime = await buildRuntimeClient(context, statusView, output);
        if (!runtime) {
            return;
        }
        const { client, endpoint, profileId } = runtime;
        try {
            const health = await client.getHealth();
            const capabilities = await client.getCapabilities();
            const capabilityCount = Array.isArray(capabilities.data?.capabilities) &&
                capabilities.data.capabilities
                ? capabilities.data.capabilities.length
                : 0;
            statusView.setSnapshot({
                connectionState: health.state,
                capabilitiesState: capabilities.state,
                endpoint,
                profileId,
                details: [`capability_count=${capabilityCount}`, `health_status=${health.statusCode ?? "none"}`]
            });
            output.appendLine(`[health] state=${health.state} status=${health.statusCode ?? "none"} capabilities_state=${capabilities.state}`);
            if (health.ok && capabilities.ok) {
                void vscode.window.showInformationMessage("Ananta backend is healthy and capabilities were loaded.");
            }
            else {
                void vscode.window.showWarningMessage(`Ananta degraded: health=${health.state}, capabilities=${capabilities.state}`);
            }
        }
        catch (error) {
            const safeError = (0, redaction_1.sanitizeErrorMessage)(error);
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
    }));
}
function deactivate() {
    // no-op
}
//# sourceMappingURL=extension.js.map