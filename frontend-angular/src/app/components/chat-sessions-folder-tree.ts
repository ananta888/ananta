import { ChatFolder, ChatSession } from '../services/chat-sessions.service';

/**
 * Pure folder-tree model of the chat sessions panel: hierarchical nodes, the
 * flattened render list and the cycle-safe ancestry check used by drag & drop.
 */

export interface FolderNode {
  folder: ChatFolder;
  sessions: ChatSession[];
  children: FolderNode[];
}

export interface TreeItem {
  trackId: string;
  kind: 'folder-header' | 'session';
  depth: number;
  folder?: FolderNode;
  session?: ChatSession;
  folderId: string;
}

export function buildChatFolderTree(folders: ChatFolder[], sessions: ChatSession[]): FolderNode[] {
  const ordered = [...folders].sort((a, b) =>
    (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  const roots = ordered.filter(f => !f.parent_id);
  return roots.map(f => ({
    folder: f,
    sessions: sessions.filter(s => s.folder_id === f.id)
      .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name)),
    children: buildChatFolderTree(ordered.filter(c => c.parent_id === f.id), sessions),
  }));
}

/** Root sessions first, then folders depth-first; collapsed folders hide their content. */
export function flattenChatFolderTree(
  folders: ChatFolder[],
  sessions: ChatSession[],
  collapsedFolders: ReadonlySet<string>,
): TreeItem[] {
  const items: TreeItem[] = [];

  for (const s of sessions.filter(s => !s.folder_id)) {
    items.push({ trackId: 'sess-' + s.id, kind: 'session', depth: 0, session: s, folderId: '' });
  }

  const addNode = (node: FolderNode, depth: number) => {
    items.push({ trackId: 'fold-' + node.folder.id, kind: 'folder-header', depth, folder: node, folderId: '' });
    if (!collapsedFolders.has(node.folder.id)) {
      for (const s of node.sessions) {
        items.push({ trackId: 'sess-' + s.id, kind: 'session', depth: depth + 1, session: s, folderId: node.folder.id });
      }
      for (const child of node.children) {
        addNode(child, depth + 1);
      }
    }
  };

  for (const node of buildChatFolderTree(folders, sessions)) {
    addNode(node, 0);
  }

  return items;
}

export function isChatFolderDescendant(folders: ChatFolder[], candidateId: string, ancestorId: string): boolean {
  let current = folders.find(folder => folder.id === candidateId);
  const seen = new Set<string>();
  while (current?.parent_id && !seen.has(current.id)) {
    if (current.parent_id === ancestorId) return true;
    seen.add(current.id);
    current = folders.find(folder => folder.id === current?.parent_id);
  }
  return false;
}
