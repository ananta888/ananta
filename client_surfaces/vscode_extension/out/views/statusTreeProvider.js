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
exports.AnantaStatusTreeProvider = void 0;
const vscode = __importStar(require("vscode"));
class StatusItem extends vscode.TreeItem {
    constructor(label, description) {
        super(label, vscode.TreeItemCollapsibleState.None);
        this.description = description;
    }
}
class AnantaStatusTreeProvider {
    onDidChangeEmitter = new vscode.EventEmitter();
    onDidChangeTreeData = this.onDidChangeEmitter.event;
    snapshot = {
        connectionState: "idle",
        capabilitiesState: "unknown",
        endpoint: "-",
        profileId: "-",
        details: []
    };
    setSnapshot(snapshot) {
        this.snapshot = snapshot;
        this.onDidChangeEmitter.fire();
    }
    refresh() {
        this.onDidChangeEmitter.fire();
    }
    getTreeItem(element) {
        return element;
    }
    getChildren() {
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
exports.AnantaStatusTreeProvider = AnantaStatusTreeProvider;
//# sourceMappingURL=statusTreeProvider.js.map