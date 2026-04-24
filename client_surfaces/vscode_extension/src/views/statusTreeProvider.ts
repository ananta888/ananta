import * as vscode from "vscode";

export interface StatusSnapshot {
  connectionState: string;
  capabilitiesState: string;
  endpoint: string;
  profileId: string;
  details: string[];
}

class StatusItem extends vscode.TreeItem {
  public constructor(label: string, description?: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
  }
}

export class AnantaStatusTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
  private readonly onDidChangeEmitter = new vscode.EventEmitter<void>();
  public readonly onDidChangeTreeData = this.onDidChangeEmitter.event;

  private snapshot: StatusSnapshot = {
    connectionState: "idle",
    capabilitiesState: "unknown",
    endpoint: "-",
    profileId: "-",
    details: []
  };

  public setSnapshot(snapshot: StatusSnapshot): void {
    this.snapshot = snapshot;
    this.onDidChangeEmitter.fire();
  }

  public refresh(): void {
    this.onDidChangeEmitter.fire();
  }

  public getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  public getChildren(): vscode.ProviderResult<vscode.TreeItem[]> {
    const details = this.snapshot.details.map((detail) => new StatusItem(detail));
    return [
      new StatusItem("Connection", this.snapshot.connectionState),
      new StatusItem("Capabilities", this.snapshot.capabilitiesState),
      new StatusItem("Endpoint", this.snapshot.endpoint),
      new StatusItem("Profile", this.snapshot.profileId),
      ...details
    ];
  }
}
