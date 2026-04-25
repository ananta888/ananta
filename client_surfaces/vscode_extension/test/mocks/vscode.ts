type DisposableLike = { dispose(): void };
type CommandHandler = (...args: unknown[]) => unknown;
type Listener<T> = (event: T) => void;

const commandHandlers = new Map<string, CommandHandler>();
const contextValues = new Map<string, unknown>();
const treeProviders = new Map<string, unknown>();
const infoMessages: string[] = [];
const warningMessages: string[] = [];
const outputLines: string[] = [];
const externalUrls: string[] = [];
const terminalCommands: string[] = [];

const quickPickQueue: unknown[] = [];
const inputBoxQueue: Array<string | undefined> = [];
const infoChoiceQueue: Array<string | undefined> = [];
const warningChoiceQueue: Array<string | undefined> = [];

const configurationValues = new Map<string, unknown>([
  ["baseUrl", "http://localhost:8080"],
  ["profileId", "default"],
  ["runtimeTarget", "local"],
  ["auth.mode", "session_token"],
  ["auth.secretStorageKey", "ananta.auth.token"],
  ["timeoutMs", 8000]
]);

const secretValues = new Map<string, string>();
let applyEditCount = 0;

function toDisposable(callback?: () => void): DisposableLike {
  return {
    dispose(): void {
      callback?.();
    }
  };
}

export class EventEmitter<T> {
  private readonly listeners = new Set<Listener<T>>();

  public readonly event = (listener: Listener<T>): DisposableLike => {
    this.listeners.add(listener);
    return toDisposable(() => this.listeners.delete(listener));
  };

  public fire(event: T): void {
    for (const listener of this.listeners) {
      listener(event);
    }
  }

  public dispose(): void {
    this.listeners.clear();
  }
}

export class ThemeIcon {
  public constructor(public readonly id: string) {}
}

export const TreeItemCollapsibleState = {
  None: 0,
  Collapsed: 1,
  Expanded: 2
} as const;

export class TreeItem {
  public description?: string;
  public tooltip?: string;
  public contextValue?: string;
  public command?: { command: string; title: string; arguments?: unknown[] };
  public iconPath?: unknown;

  public constructor(
    public readonly label: string,
    public readonly collapsibleState: (typeof TreeItemCollapsibleState)[keyof typeof TreeItemCollapsibleState]
  ) {}
}

export class Uri {
  public static parse(value: string): Uri {
    return new Uri(value);
  }

  public constructor(private readonly raw: string) {}

  public get fsPath(): string {
    if (this.raw.startsWith("file://")) {
      return decodeURIComponent(this.raw.slice("file://".length));
    }
    return this.raw;
  }

  public toString(): string {
    return this.raw;
  }
}

export class Range {
  public constructor(
    public readonly startLine: number,
    public readonly startCharacter: number,
    public readonly endLine: number,
    public readonly endCharacter: number
  ) {}
}

export const DiagnosticSeverity = {
  Error: 0,
  Warning: 1,
  Information: 2,
  Hint: 3
} as const;

export class Diagnostic {
  public constructor(
    public readonly range: Range,
    public readonly message: string,
    public readonly severity: (typeof DiagnosticSeverity)[keyof typeof DiagnosticSeverity]
  ) {}
}

export const StatusBarAlignment = {
  Left: 1,
  Right: 2
} as const;

export const ViewColumn = {
  Beside: 2
} as const;

const diagnosticsByUri = new Map<string, Diagnostic[]>();

export const languages = {
  createDiagnosticCollection(name: string): {
    set(uri: Uri, diagnostics: Diagnostic[]): void;
    delete(uri: Uri): void;
    dispose(): void;
  } {
    void name;
    return {
      set(uri: Uri, diagnostics: Diagnostic[]): void {
        diagnosticsByUri.set(uri.toString(), diagnostics);
      },
      delete(uri: Uri): void {
        diagnosticsByUri.delete(uri.toString());
      },
      dispose(): void {
        diagnosticsByUri.clear();
      }
    };
  }
};

export const commands = {
  registerCommand(commandId: string, handler: CommandHandler): DisposableLike {
    commandHandlers.set(commandId, handler);
    return toDisposable(() => commandHandlers.delete(commandId));
  },
  async executeCommand(commandId: string, ...args: unknown[]): Promise<unknown> {
    if (commandId === "setContext") {
      const [key, value] = args;
      contextValues.set(String(key), value);
      return undefined;
    }
    const handler = commandHandlers.get(commandId);
    if (!handler) {
      return undefined;
    }
    return handler(...args);
  }
};

interface ConfigurationReader {
  get<T>(key: string, defaultValue: T): T;
}

const configurationReader: ConfigurationReader = {
  get<T>(key: string, defaultValue: T): T {
    if (!configurationValues.has(key)) {
      return defaultValue;
    }
    return configurationValues.get(key) as T;
  }
};

export const workspace = {
  workspaceFolders: [] as Array<{ uri: Uri }>,
  getConfiguration(section?: string): ConfigurationReader {
    void section;
    return configurationReader;
  },
  onDidChangeConfiguration(listener: (event: { affectsConfiguration: (section: string) => boolean }) => void): DisposableLike {
    void listener;
    return toDisposable();
  },
  getWorkspaceFolder(uri: Uri): { uri: Uri } | null {
    void uri;
    return null;
  },
  async applyEdit(): Promise<boolean> {
    applyEditCount += 1;
    return true;
  }
};

export const env = {
  async openExternal(uri: Uri): Promise<boolean> {
    externalUrls.push(uri.toString());
    return true;
  }
};

export const window = {
  activeTextEditor: null as unknown,
  createOutputChannel(name: string): { appendLine(line: string): void; dispose(): void } {
    void name;
    return {
      appendLine(line: string): void {
        outputLines.push(line);
      },
      dispose(): void {
        // no-op
      }
    };
  },
  createStatusBarItem(): {
    text: string;
    tooltip: string;
    command?: string;
    show(): void;
    hide(): void;
    dispose(): void;
  } {
    return {
      text: "",
      tooltip: "",
      command: undefined,
      show(): void {
        // no-op
      },
      hide(): void {
        // no-op
      },
      dispose(): void {
        // no-op
      }
    };
  },
  registerTreeDataProvider(viewId: string, provider: unknown): DisposableLike {
    treeProviders.set(viewId, provider);
    return toDisposable(() => treeProviders.delete(viewId));
  },
  async showQuickPick<T>(items: readonly T[]): Promise<T | undefined> {
    const queued = quickPickQueue.shift();
    if (queued === undefined) {
      return items[0];
    }
    if (typeof queued === "number") {
      return items[Math.max(0, Math.min(items.length - 1, queued))];
    }
    if (typeof queued === "string") {
      return items.find((entry) => JSON.stringify(entry).includes(queued));
    }
    return queued as T;
  },
  async showInputBox(): Promise<string | undefined> {
    return inputBoxQueue.shift();
  },
  async showInformationMessage<T extends string>(message: string, ...items: T[]): Promise<T | undefined> {
    void items;
    infoMessages.push(message);
    const queued = infoChoiceQueue.shift();
    return queued as T | undefined;
  },
  async showWarningMessage<T extends string>(message: string, ...items: T[]): Promise<T | undefined> {
    void items;
    warningMessages.push(message);
    const queued = warningChoiceQueue.shift();
    return queued as T | undefined;
  },
  async showErrorMessage<T extends string>(message: string, ...items: T[]): Promise<T | undefined> {
    void items;
    warningMessages.push(message);
    const queued = warningChoiceQueue.shift();
    return queued as T | undefined;
  },
  createWebviewPanel(): { webview: { html: string } } {
    return {
      webview: {
        html: ""
      }
    };
  },
  createTerminal(options: { name: string; env?: Record<string, string> }): { show(preserveFocus?: boolean): void; sendText(text: string): void } {
    void options;
    return {
      show(): void {
        // no-op
      },
      sendText(text: string): void {
        terminalCommands.push(text);
      }
    };
  }
};

export function __resetMock(): void {
  commandHandlers.clear();
  contextValues.clear();
  treeProviders.clear();
  infoMessages.length = 0;
  warningMessages.length = 0;
  outputLines.length = 0;
  externalUrls.length = 0;
  terminalCommands.length = 0;
  quickPickQueue.length = 0;
  inputBoxQueue.length = 0;
  infoChoiceQueue.length = 0;
  warningChoiceQueue.length = 0;
  secretValues.clear();
  applyEditCount = 0;
  configurationValues.clear();
  configurationValues.set("baseUrl", "http://localhost:8080");
  configurationValues.set("profileId", "default");
  configurationValues.set("runtimeTarget", "local");
  configurationValues.set("auth.mode", "session_token");
  configurationValues.set("auth.secretStorageKey", "ananta.auth.token");
  configurationValues.set("timeoutMs", 8000);
}

export function __setConfig(values: Record<string, unknown>): void {
  for (const [key, value] of Object.entries(values)) {
    configurationValues.set(key, value);
  }
}

export function __queueQuickPick(value: unknown): void {
  quickPickQueue.push(value);
}

export function __queueInputBox(value: string | undefined): void {
  inputBoxQueue.push(value);
}

export function __queueInformationChoice(value: string | undefined): void {
  infoChoiceQueue.push(value);
}

export function __queueWarningChoice(value: string | undefined): void {
  warningChoiceQueue.push(value);
}

export function __setSecret(key: string, value: string): void {
  secretValues.set(key, value);
}

export function __createExtensionContext(): {
  subscriptions: DisposableLike[];
  secrets: {
    get(key: string): Promise<string | undefined>;
    store(key: string, value: string): Promise<void>;
    delete(key: string): Promise<void>;
  };
} {
  return {
    subscriptions: [],
    secrets: {
      async get(key: string): Promise<string | undefined> {
        return secretValues.get(key);
      },
      async store(key: string, value: string): Promise<void> {
        secretValues.set(key, value);
      },
      async delete(key: string): Promise<void> {
        secretValues.delete(key);
      }
    }
  };
}

export function __getRegisteredCommands(): string[] {
  return Array.from(commandHandlers.keys()).sort();
}

export function __getContextValue(key: string): unknown {
  return contextValues.get(key);
}

export function __getInformationMessages(): string[] {
  return [...infoMessages];
}

export function __getWarningMessages(): string[] {
  return [...warningMessages];
}

export function __getOutputLines(): string[] {
  return [...outputLines];
}

export function __getExternalUrls(): string[] {
  return [...externalUrls];
}

export function __getTerminalCommands(): string[] {
  return [...terminalCommands];
}

export function __getApplyEditCount(): number {
  return applyEditCount;
}
