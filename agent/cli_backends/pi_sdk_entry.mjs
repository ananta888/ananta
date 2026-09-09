/** One delegated, no-tools turn. No CLI startup migrations or resource discovery. */
import { readFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { pathToFileURL } from "node:url";

let session;
try {
  const [sdkPath, configDirectory] = process.argv.slice(2);
  const [major, minor] = process.versions.node.split(".").map(Number);
  if (major < 22 || (major === 22 && minor < 19) || process.argv.length !== 4
      || !isAbsolute(sdkPath) || !isAbsolute(configDirectory)
      || process.env.PI_CODING_AGENT_DIR !== configDirectory) {
    throw new Error("pi_sdk_configuration_invalid");
  }
  const input = readFileSync(0, "utf8");
  if (!input.trim() || input.length > 200001) throw new Error("pi_prompt_invalid");
  const sdk = await import(pathToFileURL(sdkPath).href);
  const settings = JSON.parse(readFileSync(join(configDirectory, "settings.json"), "utf8"));
  const modelConfig = JSON.parse(readFileSync(join(configDirectory, "models.json"), "utf8"));
  if (Object.keys(modelConfig.providers).join() !== "ananta"
      || modelConfig.providers.ananta.models.length !== 1) throw new Error("pi_model_binding_invalid");
  const modelId = modelConfig.providers.ananta.models[0].id;
  const maxTokens = modelConfig.providers.ananta.models[0].maxTokens;
  if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 16384) throw new Error("pi_budget_invalid");
  const settingsManager = sdk.SettingsManager.inMemory(settings, { projectTrusted: false });
  const modelRuntime = await sdk.ModelRuntime.create({
    authPath: join(configDirectory, "auth.json"), modelsPath: join(configDirectory, "models.json"),
    modelsStorePath: join(configDirectory, "models-cache.json"), allowModelNetwork: false, refreshOnCreate: false,
  });
  const model = modelRuntime.getModel("ananta", modelId);
  if (!model || model.provider !== "ananta" || model.id !== modelId) throw new Error("pi_model_unavailable");
  const resourceLoader = new sdk.DefaultResourceLoader({
    cwd: process.cwd(), agentDir: configDirectory, settingsManager,
    noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
    systemPrompt: readFileSync(join(configDirectory, "hub-system.md"), "utf8"),
    appendSystemPrompt: [readFileSync(join(configDirectory, "hub-append.md"), "utf8")],
  });
  await resourceLoader.reload();
  ({ session } = await sdk.createAgentSession({
    cwd: process.cwd(), agentDir: configDirectory, modelRuntime, model, thinkingLevel: "off",
    noTools: "all", tools: [], customTools: [], scopedModels: [{ model, thinkingLevel: "off" }],
    resourceLoader, settingsManager, sessionManager: sdk.SessionManager.inMemory(process.cwd()),
  }));
  session.agent.shouldStopAfterTurn = () => true;
  const stream = session.agent.streamFunction;
  let calls = 0;
  session.agent.streamFunction = (selected, context, options) => {
    if (++calls !== 1 || selected.id !== modelId || selected.provider !== "ananta"
        || selected.api !== "openai-completions" || context.tools?.length) {
      throw new Error("pi_model_call_not_authorized");
    }
    return stream(selected, context, { ...options, maxTokens, maxRetries: 0 });
  };
  let pending = Promise.resolve();
  const write = (event) => {
    // Drop cumulative streaming snapshots, matching the pinned JSON projection.
    if (event.type === "message_update") {
      const { partial: _partial, ...update } = event.assistantMessageEvent;
      event = { type: event.type, usage: event.message.usage, assistantMessageEvent: update };
    }
    const record = JSON.stringify(event) + "\n";
    pending = pending.then(() => new Promise((resolve, reject) => {
      process.stdout.write(record, (error) => error ? reject(error) : resolve());
    }));
    return pending;
  };
  await write(session.sessionManager.getHeader());
  session.subscribe(write);
  session.agent.subscribe(async () => { await pending; });
  await session.prompt(input.trimEnd(), { expandPromptTemplates: false });
  await session.waitForIdle();
  await pending;
  if (calls !== 1) throw new Error("pi_model_call_missing");
} catch {
  // Never project package exception text, prompt content or credential material.
  process.stderr.write("pi_sdk_execution_failed\n");
  process.exitCode = 1;
} finally {
  session?.dispose();
}
