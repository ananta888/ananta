"""Read/ACK seam: a revoked browser queue cannot dispatch a stale Hub input."""

CHAT_OPERATION = """([operation, cursor]) => {
  const chat = window.anantaMachine.chat;
  try {
    if (operation === 'poll') return {state:'ok', value:chat.poll()};
    if (operation === 'ack') { chat.ack(cursor); return {state:'ok', value:null}; }
    throw new Error('meet_dialog_browser_operation_invalid');
  } catch (error) {
    const revoked = ['meet_chat_authority_changed', 'meet_chat_authority_unavailable',
      'meet_chat_receive_denied', 'meet_chat_closed'].includes(error?.message);
    if (revoked && chat.status().open === false) return {state:'closed'};
    throw error;
  }
}"""


class DialogChatBrowser:
    def __init__(self, page):
        self.page = page

    def _call(self, operation, cursor=None):
        result = self.page.evaluate(CHAT_OPERATION, [operation, cursor])
        if result == {"state": "closed"}:
            return None
        if not isinstance(result, dict) or set(result) != {"state", "value"} or result["state"] != "ok":
            raise ValueError("meet_dialog_chat_operation_invalid")
        return result

    def poll(self):
        result = self._call("poll")
        if result is None:
            return None
        if not isinstance(result["value"], dict):
            raise ValueError("meet_dialog_chat_batch_invalid")
        return result["value"]

    def ack(self, cursor):
        result = self._call("ack", cursor)
        if result is None:
            return False
        if result["value"] is not None:
            raise ValueError("meet_dialog_chat_operation_invalid")
        return True
