/** Component styles of the chat sessions panel (see ChatSessionsPanelComponent). */
export const CHAT_SESSIONS_PANEL_STYLES = `
    :host { display: block; }
    .sessions-panel { display: flex; flex-direction: column; }

    /* ── Top bar ── */
    .top-bar {
      display: flex; align-items: center; gap: 6px;
      padding: 5px 8px; border-bottom: 1px solid #0d1c30;
    }
    .profile-manager { padding: 8px; border-bottom: 1px solid #1a2d4a; background: #091526; display: grid; gap: 6px; }
    .profile-row { display: grid; grid-template-columns: 1fr auto auto auto; align-items: center; gap: 5px; font-size: 11px; }
    .profile-editor { display: grid; grid-template-columns: auto 1fr; gap: 6px; padding-top: 6px; border-top: 1px dashed #1a2d4a; }
    .profile-editor textarea, .profile-editor .row { grid-column: 1 / -1; }
    .reorg-btn {
      background: #0a1825; border: 1px solid #1a3050; color: #7ab0e0;
      padding: 4px 10px; cursor: pointer; font-size: 11px; border-radius: 3px; flex: 1;
    }
    .reorg-btn:hover:not(:disabled) { background: #102238; color: #7fffd4; border-color: #2a5090; }
    .reorg-btn:disabled { opacity: 0.45; cursor: default; }
    .new-folder-btn {
      background: #0a1825; border: 1px solid #1a3050; color: #7ab0e0;
      padding: 4px 8px; cursor: pointer; font-size: 11px; border-radius: 3px; flex-shrink: 0;
    }
    .new-folder-btn:hover { background: #102238; color: #7fffd4; border-color: #2a5090; }

    /* ── Root drop zone ── */
    .root-drop-zone {
      border: 1px dashed #3a6a9a; padding: 6px; font-size: 10px;
      color: #6b8ab8; text-align: center; margin: 4px 6px; border-radius: 3px;
    }
    .root-drop-zone.drag-over { border-color: #3aacca; background: #0a2030; }
    .loading-indicator { color: #3a6a9a; font-size: 13px; animation: spin 1s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── Folder tree ── */
    .list { display: flex; flex-direction: column; }
    .folder-row {
      display: flex; align-items: center; gap: 4px;
      padding: 4px 6px; cursor: pointer; border-bottom: 1px solid #0d1c30;
    }
    .folder-row:hover { background: #0a1825; }
    .folder-row.drag-over { background: #0a2030; border: 1px dashed #3aacca; }
    .folder-chevron { font-size: 9px; color: #3a6a9a; width: 12px; flex-shrink: 0; }
    .folder-icon { font-size: 13px; flex-shrink: 0; }
    .folder-name {
      flex: 1; font-size: 11px; color: #7ab0e0;
      min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }

    /* ── Session rows ── */
    .session-row {
      display: flex; align-items: center; gap: 3px;
      padding: 2px 6px; border-bottom: 1px solid #152040;
    }
    .session-row.active { background: #0e2038; }
    .session-row[draggable] { cursor: grab; }
    .session-row.drag-over { background: #0a2030; border: 1px dashed #3aacca; }

    /* ── Icon wrap + tooltip ── */
    .icon-wrap { position: relative; display: inline-flex; align-items: center; flex-shrink: 0; }
    .session-tooltip {
      position: absolute; left: 22px; top: 0; z-index: 100;
      background: #071624; border: 1px solid #1a3a5a; border-radius: 4px;
      padding: 7px 10px; min-width: 180px; max-width: 240px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.5); pointer-events: none;
    }
    .tt-name { font-weight: 600; color: #7fffd4; font-size: 12px; margin-bottom: 3px; }
    .tt-type { font-size: 10px; color: #3aacca; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }
    .tt-desc { font-size: 10px; color: #8aaccc; line-height: 1.4; margin-bottom: 4px; }
    .tt-preview { font-size: 10px; color: #6a8aaa; font-style: italic; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .tt-muted { color: #3a5a7a; font-size: 10px; }
    .tt-row { font-size: 10px; color: #8aaac8; margin-bottom: 2px; }

    .session-btn {
      flex: 1; min-width: 0; display: flex; align-items: center; gap: 5px;
      background: transparent; border: none; color: #c8d8f8; padding: 6px 4px;
      cursor: pointer; text-align: left; font-size: 12px; border-radius: 2px;
    }
    .session-btn:hover { color: #7fffd4; }
    .session-row.active .session-btn { color: #7fffd4; font-weight: 500; }

    .sess-icon { font-size: 13px; flex-shrink: 0; }
    .sess-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .active-dot { color: #7fffd4; font-size: 7px; flex-shrink: 0; }
    .delta-badge {
      background: #0a2a3a; border: 1px solid #1a5a7a; color: #3aacca;
      font-size: 9px; padding: 1px 4px; border-radius: 8px; flex-shrink: 0;
    }

    /* ── Icon buttons ── */
    .icon-btn {
      background: transparent; border: none; color: #2a4a6a; padding: 3px 5px;
      cursor: pointer; font-size: 12px; flex-shrink: 0; border-radius: 2px;
    }
    .icon-btn:hover:not(:disabled) { color: #c8d8f8; background: #102030; }
    .icon-btn.del:hover:not(:disabled) { color: #fb7185; background: #1a0a0a; }
    .icon-btn.ok:hover { color: #7fffd4; }
    .icon-btn:disabled { opacity: 0.25; cursor: default; }
    .icon-btn.cfg { font-size: 13px; }
    .icon-btn.cfg.cfg-open { color: #7fffd4; }

    /* ── Rename input ── */
    .name-input {
      flex: 1; background: #0f1c30; border: 1px solid #2a4070; color: #c8d8f8;
      padding: 3px 6px; font-size: 12px; font-family: inherit; border-radius: 2px;
    }

    /* ── Per-session config panel ── */
    .cfg-panel {
      padding: 8px 10px 10px; background: #08131f;
      border-bottom: 1px solid #1a3050;
      display: flex; flex-direction: column; gap: 6px;
    }
    .cfg-hint {
      font-size: 10px; color: #3a6a9a; display: flex; align-items: center; gap: 6px;
    }
    .cfg-hint-badge {
      background: #0a2a3a; border: 1px solid #1a4a6a; color: #3a8aaa;
      font-size: 9px; padding: 1px 5px; border-radius: 8px;
    }

    /* ── Setting row: label + control + delta dot ── */
    .cfg-row {
      display: grid; grid-template-columns: 90px 1fr 16px; gap: 5px; align-items: center;
    }
    .cfg-label { font-size: 11px; color: #6b8ab8; white-space: nowrap; }
    .cfg-label-block {
      display: flex; flex-direction: column; gap: 3px;
      font-size: 11px; color: #6b8ab8;
    }
    .cfg-label-block.inline { flex-direction: row; align-items: center; gap: 7px; color: #c8d8f8; }

    select, input[type="text"], input[type="number"], textarea {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      padding: 3px 5px; font-family: inherit; font-size: 11px; border-radius: 2px;
    }
    textarea { resize: vertical; }

    /* ── Delta dot: ○ inherited, ● overridden ── */
    .delta-dot {
      color: #2a4a6a; font-size: 11px; cursor: pointer; user-select: none;
      justify-self: center; transition: color 0.15s;
    }
    .delta-dot.on { color: #3aacca; }
    .delta-dot.on:hover { color: #fb7185; }

    /* ── Checkboxes with inline delta dot ── */
    .cfg-checkboxes { display: flex; flex-direction: column; gap: 4px; }
    .cfg-check {
      display: flex; align-items: center; gap: 6px;
      font-size: 11px; color: #c8d8f8; cursor: pointer;
    }
    .cfg-check.overridden { color: #a8c8f0; }
    .delta-dot-inline {
      color: #2a4a6a; font-size: 10px; cursor: pointer; margin-left: auto;
    }
    .delta-dot-inline.on { color: #3aacca; }
    .delta-dot-inline.on:hover { color: #fb7185; }

    .reset-all-btn {
      background: #0a1a2a; border: 1px solid #1a3a5a; color: #3a8aaa;
      padding: 3px 8px; cursor: pointer; font-size: 10px; border-radius: 2px;
      align-self: flex-start;
    }
    .reset-all-btn:hover { color: #fb7185; border-color: #4a1a1a; }
    .close-cfg-btn {
      background: transparent; border: 1px solid #1a2d4a; color: #4a6a9a;
      padding: 3px 8px; cursor: pointer; font-size: 10px; align-self: flex-end;
      border-radius: 2px;
    }
    .close-cfg-btn:hover { color: #c8d8f8; }

    /* ── PUG preset section ── */
    .pug-section {
      background: #07111e; border: 1px solid #1a3050; border-radius: 3px;
      padding: 7px 9px; display: flex; flex-direction: column; gap: 5px;
    }
    .pug-title { font-size: 10px; color: #3a7aaa; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; }
    .pug-preset-bar { display: flex; gap: 4px; align-items: center; }
    .pug-btn {
      background: transparent; border: 1px solid #1a3050; color: #4a6a9a;
      padding: 2px 8px; cursor: pointer; font-size: 10px; border-radius: 2px;
    }
    .pug-btn:hover { color: #c8d8f8; }
    .pug-btn.active { color: #7fffd4; border-color: #2a6a7a; background: #0a2030; }
    .pug-custom { font-size: 10px; color: #7a5a3a; border: 1px solid #3a2a1a; padding: 2px 6px; border-radius: 2px; }
    .pug-desc { font-size: 10px; color: #4a6a8a; line-height: 1.5; }

    /* ── Context / History section ── */
    .ctx-section {
      background: #07111e; border: 1px solid #1a3050; border-radius: 3px;
      padding: 7px 9px; display: flex; flex-direction: column; gap: 5px;
    }
    .ctx-title { font-size: 10px; color: #3a7aaa; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; }
    .ctx-row { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
    .ctx-row-label { font-size: 10px; color: #6b8ab8; width: 88px; flex-shrink: 0; }
    .ctx-toggle-lbl {
      font-size: 10px; color: #8aaac8; display: flex; align-items: center; gap: 4px; cursor: pointer;
    }
    .ctx-num {
      width: 46px; padding: 2px 4px; font-size: 10px;
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      border-radius: 2px; font-family: inherit;
    }
    .ctx-num.wide { width: 64px; }
    .ctx-unit { font-size: 10px; color: #4a6a8a; white-space: nowrap; }
    .ctx-select {
      font-size: 10px; padding: 2px 4px;
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      border-radius: 2px; font-family: inherit;
    }
    .ctx-btn-row { display: flex; gap: 5px; margin-top: 2px; }
    .ctx-overview-btn {
      background: transparent; border: 1px solid #1a2d4a; color: #4a6a9a;
      padding: 2px 8px; cursor: pointer; font-size: 10px; align-self: flex-start;
      border-radius: 2px; margin-top: 2px;
    }
    .ctx-btn-row .ctx-overview-btn { margin-top: 0; }
    .ctx-overview-btn:hover { color: #7fffd4; border-color: #2a5080; }
    .ctx-table { font-size: 10px; border-collapse: collapse; width: 100%; margin-top: 4px; }
    .ctx-td-label { color: #6b8ab8; padding: 2px 6px 2px 0; width: 100px; white-space: nowrap; }
    .ctx-td-val { color: #9ab8d8; padding: 2px 0; }
    .ctx-loading { font-size: 10px; color: #3a6a9a; padding: 4px 0; }
    .ctx-clear-btn {
      background: transparent; border: 1px solid #2a1a1a; color: #7a4a4a;
      padding: 2px 8px; cursor: pointer; font-size: 10px; align-self: flex-start;
      border-radius: 2px; margin-top: 2px;
    }
    .ctx-clear-btn:hover { color: #fb7185; border-color: #4a1a1a; }

    /* ── Bottom bar + new form ── */
    .bottom-bar { padding: 6px 8px; border-top: 1px solid #152040; }
    .new-btn {
      background: transparent; border: 1px solid #1a2d4a; color: #6b8ab8;
      padding: 5px 10px; cursor: pointer; font-size: 11px; width: 100%; border-radius: 2px;
    }
    .new-btn:hover { color: #c8d8f8; border-color: #2a4070; }

    .new-form {
      padding: 10px; background: #08131f; border-top: 1px solid #1a2d4a;
      display: flex; flex-direction: column; gap: 7px;
    }
    .new-form-label { font-size: 10px; color: #6b8ab8; text-transform: uppercase; letter-spacing: 0.05em; }
    .new-form-row { display: flex; gap: 6px; }
    .icon-field { width: 46px; flex-shrink: 0; }
    .name-field { flex: 1; }
    .group-field { flex: 1; }
    .create-btn {
      background: #102238; border: 1px solid #2a5090; color: #7fffd4;
      padding: 6px 10px; cursor: pointer; font-size: 12px; border-radius: 2px;
    }
    .create-btn:disabled { opacity: 0.35; cursor: default; }
    .create-btn:not(:disabled):hover { background: #183250; }

    /* ── Session type grid ── */
    .type-grid {
      display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px;
    }
    .type-btn {
      display: flex; flex-direction: column; align-items: center; gap: 2px;
      background: #07111e; border: 1px solid #1a2d4a; color: #6b8ab8;
      padding: 5px 4px; cursor: pointer; border-radius: 3px; font-size: 10px;
    }
    .type-btn:hover { background: #0a1825; border-color: #2a4070; color: #c8d8f8; }
    .type-btn.selected { border-color: #2a6a7a; background: #0a2030; color: #7fffd4; }
    .type-icon { font-size: 14px; }
    .type-label { font-size: 9px; white-space: nowrap; }

    .err { color: #fb7185; font-size: 11px; padding: 5px 10px; }

    /* ── AI Reorganize modal ── */
    .modal-backdrop {
      position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 200;
      display: flex; align-items: center; justify-content: center;
    }
    .modal {
      background: #07111e; border: 1px solid #1a3050; border-radius: 6px;
      padding: 20px; min-width: 360px; max-width: 500px; max-height: 80vh; overflow-y: auto;
    }
    .modal-title { font-size: 14px; font-weight: 600; color: #7fffd4; margin-bottom: 12px; }
    .modal-summary { font-size: 11px; color: #6b8ab8; margin-bottom: 14px; line-height: 1.5; }
    .proposal-folder { margin-bottom: 10px; }
    .proposal-folder-header { font-size: 12px; color: #7ab0e0; font-weight: 500; margin-bottom: 4px; }
    .proposal-session-list { padding-left: 14px; }
    .proposal-session-item { font-size: 11px; color: #9ab8d8; padding: 2px 0; }
    .modal-actions { display: flex; gap: 8px; margin-top: 16px; justify-content: flex-end; }
    .btn-accept {
      background: #102238; border: 1px solid #2a5090; color: #7fffd4;
      padding: 6px 14px; cursor: pointer; font-size: 12px; border-radius: 3px;
    }
    .btn-accept:hover { background: #183250; }
    .btn-cancel {
      background: transparent; border: 1px solid #1a2d4a; color: #4a6a9a;
      padding: 6px 14px; cursor: pointer; font-size: 12px; border-radius: 3px;
    }
    .btn-cancel:hover { color: #c8d8f8; }

    /* ── Prompt preview modal ── */
    .preview-modal { min-width: 420px; max-width: 620px; }
    .preview-sections { display: flex; flex-direction: column; gap: 3px; margin-bottom: 10px; }
    .preview-sec-row { display: flex; align-items: center; gap: 8px; font-size: 11px; }
    .preview-sec-name { color: #c8d8f8; width: 130px; flex-shrink: 0; }
    .preview-badge {
      font-size: 9px; padding: 1px 6px; border-radius: 8px;
      background: #101a28; border: 1px solid #1a2d4a; color: #4a6a8a;
    }
    .preview-badge.on { background: #0a2a1a; border-color: #2a5a4a; color: #7fffd4; }
    .preview-chars { color: #6b8ab8; font-size: 10px; }
    .preview-trunc { color: #d8a848; font-size: 10px; }
    .preview-total { font-size: 11px; color: #9ab8d8; margin-bottom: 8px; }
    .preview-pre {
      max-height: 300px; overflow-y: auto; font-size: 10px;
      background: #050d18; border: 1px solid #1a2d4a; border-radius: 3px;
      padding: 8px; color: #9ab8d8; white-space: pre-wrap; word-break: break-word;
      margin: 0;
    }
`;
