/**
 * Component styles of {@link AiSnakeSharePanelComponent}, kept beside the
 * component so the component file only carries template and behavior.
 */
export const AI_SNAKE_SHARE_PANEL_STYLES = `
    :host { font-family: ui-monospace, Menlo, Consolas, monospace; }
    .share-panel { display: flex; flex-direction: column; height: 100%; background: #0b1220; color: #c8d8f8; font-size: 12px; }
    .share-header { padding: 7px 10px; border-bottom: 1px solid #1a2d4a; background: #0d1828; font-weight: 600; display: flex; align-items: center; gap: 8px; flex-shrink: 0; flex-wrap: wrap; }
    .main-tabs { margin-left: auto; display: flex; gap: 0; border: 1px solid #1a2d4a; border-radius: 3px; overflow: hidden; }
    .main-tab { background: transparent; border: none; border-right: 1px solid #1a2d4a; color: #4a6a9a; cursor: pointer; font-size: 10px; font-family: inherit; padding: 2px 8px; }
    .main-tab:last-child { border-right: none; }
    .main-tab.active { color: #7fffd4; background: #102238; }
    .groups-panel { flex: 1; overflow-y: auto; padding: 8px 10px; }
    .groups-toolbar { margin-bottom: 8px; }
    .group-row { display: flex; align-items: center; gap: 6px; padding: 5px 0; border-bottom: 1px solid #0f1828; cursor: pointer; }
    .group-row:hover { background: #0d1828; }
    .group-name { font-weight: 600; color: #a8c7ff; flex: 1; }
    .group-desc { font-size: 10px; color: #4a6a9a; flex: 2; }
    .share-btn.sm { padding: 2px 7px; font-size: 10px; }
    .group-detail { padding: 4px 0; }
    .group-members-header { margin: 6px 0 4px; }
    .group-member-row { display: flex; align-items: center; gap: 6px; padding: 3px 0; border-bottom: 1px solid #0f1828; }
    .group-member-name { flex: 1; color: #a8c7ff; font-size: 11px; }
    .add-member-row { display: flex; gap: 6px; margin-top: 8px; }
    .invite-result { margin-top: 8px; display: flex; align-items: center; gap: 6px; background: #0d1828; padding: 6px 8px; border-radius: 3px; }
    .share-badge { font-size: 10px; padding: 1px 6px; border-radius: 2px; border: 1px solid #1a2d4a; }
    .share-badge.active { color: #7fffd4; border-color: #7fffd4; }
    .share-badge.owner { color: #fbbf24; border-color: #7a5a10; }
    .share-badge.participant { color: #a8c7ff; border-color: #2a4070; }
    .share-actions { display: flex; flex-direction: column; gap: 8px; padding: 12px 10px; }
    .pair-session-toolbar { display: grid; gap: 6px; padding: 8px 10px; border-bottom: 1px solid #1a2d4a; }
    .pair-session-toolbar-secondary { display: flex; flex-wrap: wrap; gap: 5px; }
    .pair-session-toolbar-secondary .share-btn { flex: 1 1 auto; text-align: center; }
    .share-btn.selected { border-color: #7fffd4; color: #7fffd4; background: #102238; }
    .quick-share { font-weight: 700; }
    .quick-share-note { color: #7f9bbd; font-size: 10px; line-height: 1.4; }
    .share-btn {
      border: 1px solid #1a2d4a; border-radius: 3px; padding: 6px 10px; background: transparent;
      color: #6b8ab8; cursor: pointer; font-size: 12px; font-family: inherit; text-align: left;
    }
    .share-btn:hover:not([disabled]) { border-color: #2a4070; color: #c8d8f8; }
    .share-btn.primary { background: #162444; border-color: #2a4070; color: #a8c7ff; }
    .share-btn.primary:hover:not([disabled]) { background: #1e3058; border-color: #7fffd4; color: #7fffd4; }
    .share-btn.danger { color: #fb7185; border-color: #4a1a1a; background: #1a0a0a; }
    .share-btn[disabled] { opacity: 0.4; cursor: not-allowed; }
    .share-form { padding: 10px; display: flex; flex-direction: column; gap: 8px; }
    .share-form-title { font-weight: 600; color: #a8c7ff; margin-bottom: 2px; }
    .share-label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: #6b8ab8; }
    .share-input, .share-select {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      padding: 4px 7px; font-size: 11px; font-family: inherit; border-radius: 2px;
    }
    .share-input.mono { letter-spacing: 0.1em; }
    .legacy-consent { display: flex; align-items: flex-start; gap: 5px; color: #fbbf24; font-size: 10px; line-height: 1.35; }
    .share-checks { display: flex; gap: 12px; font-size: 11px; }
    .share-checks label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
    .share-form-actions { display: flex; gap: 8px; }
    .share-error { color: #fb7185; font-size: 11px; }
    .share-session-info { padding: 8px 10px; border-bottom: 1px solid #1a2d4a; }
    .share-session-title { font-weight: 600; color: #a8c7ff; margin-bottom: 4px; }
    .compact-peer-state { display: flex; flex-direction: column; gap: 3px; padding: 6px 10px; border-bottom: 1px solid #1a2d4a; color: #8fb2d9; font-size: 10px; }
    .compact-peer-state strong { color: #7fffd4; }
    .compact-peer-state small { color: #607a9a; }
    .compact-share-consent { display: flex; flex-direction: column; gap: 5px; padding: 6px 10px; border-bottom: 1px solid #1a2d4a; color: #8fb2d9; font-size: 10px; }
    .compact-share-consent strong { color: #a8c7ff; }
    .compact-share-actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .security-line { display: flex; flex-direction: column; gap: 3px; margin-top: 6px; padding: 5px 6px; border: 1px solid #2a4070; color: #a8c7ff; }
    .security-line.ready { color: #7fffd4; border-color: #1a4a2a; }
    .security-line.warning { color: #fbbf24; border-color: #7a5a10; }
    .security-line.failed { color: #fb7185; border-color: #4a1a1a; }
    .security-line code { overflow-wrap: anywhere; font-size: 9px; }
    .share-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .share-meta-code { font-size: 11px; color: #6b8ab8; }
    .share-meta-code strong { color: #7fffd4; letter-spacing: 0.08em; }
    .share-copy-btn { background: none; border: 1px solid #1a2d4a; color: #6b8ab8; cursor: pointer; padding: 1px 5px; border-radius: 2px; font-size: 12px; }
    .share-copy-btn:hover { border-color: #7fffd4; color: #7fffd4; }
    .share-tabs { display: flex; border-bottom: 1px solid #1a2d4a; flex-shrink: 0; }
    .share-tab { flex: 1; padding: 5px; background: none; border: none; border-bottom: 2px solid transparent; color: #4a6a9a; cursor: pointer; font-family: inherit; font-size: 11px; }
    .share-tab.active { color: #7fffd4; border-bottom-color: #7fffd4; }
    .share-chat-msgs { flex: 1; overflow-y: auto; padding: 6px 8px; min-height: 0; max-height: 200px; }
    .share-chat-msgs::-webkit-scrollbar { width: 4px; }
    .share-chat-msgs::-webkit-scrollbar-thumb { background: #1a2d4a; }
    .share-msg { margin-bottom: 5px; display: flex; flex-direction: column; }
    .share-msg.own .share-msg-text { background: #162238; border-color: #2a4070; color: #a8c7ff; align-self: flex-end; }
    .share-msg-sender { font-size: 10px; color: #4a6a9a; margin-bottom: 2px; }
    .share-msg-text { background: #0f1c30; border: 1px solid #1a3058; padding: 4px 8px; border-radius: 2px; color: #c8d8f8; display: inline-block; max-width: 90%; word-break: break-word; }
    .share-chat-input-row { display: flex; gap: 6px; padding: 6px 8px; border-top: 1px solid #1a2d4a; flex-shrink: 0; }
    .chat-error { padding: 0 8px 6px; }
    .share-chat-input { flex: 1; background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8; padding: 4px 7px; font-size: 11px; font-family: inherit; border-radius: 2px; }
    .share-send-btn { background: #162444; border: 1px solid #2a4070; color: #a8c7ff; padding: 4px 10px; cursor: pointer; border-radius: 2px; font-size: 13px; }
    .share-send-btn:hover:not([disabled]) { border-color: #7fffd4; color: #7fffd4; }
    .share-participants { flex: 1; overflow-y: auto; padding: 6px 8px; max-height: 200px; }
    .share-participant { padding: 5px 0; border-bottom: 1px solid #0f1828; }
    .share-participant.revoked { opacity: 0.4; }
    .share-p-row { display: flex; align-items: center; gap: 8px; }
    .share-p-id { flex: 1; font-size: 11px; color: #a8c7ff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .share-p-status { font-size: 10px; color: #4a6a9a; }
    .share-p-status.online { color: #7fffd4; }
    .share-revoke-btn { background: none; border: 1px solid #4a1a1a; color: #fb7185; cursor: pointer; padding: 1px 5px; border-radius: 2px; font-size: 10px; }
    .share-p-perms { display: flex; gap: 4px; margin-top: 3px; flex-wrap: wrap; }
    .share-perm-chip { font-size: 9px; padding: 1px 5px; border: 1px solid #131e36; border-radius: 2px; color: #2a4070; }
    .share-perm-chip.on { color: #7fffd4; border-color: #1a4a2a; }
    .share-empty { color: #2a4070; font-size: 11px; padding: 8px 0; }
    .share-footer { padding: 8px 10px; border-top: 1px solid #1a2d4a; flex-shrink: 0; }
`;
