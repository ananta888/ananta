"""Read-only bounded DOM projection; source pixels never cross this boundary.

Use only in an independently admitted, script-disabled ephemeral public page.
This is conservative structural filtering, not semantic DLP or proof that an
arbitrary page is public. Source admission/navigation remain separate duties.
"""

from ananta_contracts.browser_public_view import blocked_view, validate_public_view

# No selectors, script text, URLs or action code supplied by the source/Hub are
# interpolated into this program. This always returns a truthy closed object,
# including denials: the bounded wait evaluates once, never retries a denial.
SNAPSHOT_SCRIPT = r"""() => {
  const blocked = reason => ({schema:'ananta.browser-public-view.v1',state:'blocked',reason,blocks:[]});
  if (innerWidth !== 640 || innerHeight !== 360) return blocked('source_unavailable');
  if (!document.body) return blocked('no_visible_text');
  const denied = new Set(['INPUT','TEXTAREA','SELECT','BUTTON','FORM','DATALIST','OPTION']);
  const opaque = new Set(['CANVAS','VIDEO','AUDIO','IMG','PICTURE','IFRAME','FRAME','FRAMESET','SVG',
    'OBJECT','EMBED','APPLET','PORTAL']);
  const ignored = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','HEAD']);
  const ordinary = new Set(('BODY A ABBR ADDRESS ARTICLE ASIDE B BLOCKQUOTE BR CAPTION CITE CODE COL COLGROUP '
    + 'DD DEL DETAILS DFN DIV DL DT EM FIGCAPTION FIGURE FOOTER H1 H2 H3 H4 H5 H6 HEADER HGROUP HR I INS KBD '
    + 'LI MAIN MARK NAV OL P PRE Q RP RT RUBY S SAMP SECTION SMALL SPAN STRONG SUB SUMMARY SUP TABLE TBODY TD '
    + 'TFOOT TH THEAD TIME TR U UL VAR WBR').split(' '));
  const sensitive = new RegExp('(?:^|[\\s_\\-:])(?:password|passwort|secret|token|api[-_ ]?key|credential|'
    + 'confidential|private|login|signin|oauth|vertraulich)(?:$|[\\s_\\-:])', 'i');
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  const blocks = []; let node = document.body, nodes = 0, chars = 0, bytes = 0;
  const encoder = new TextEncoder(), styles = new WeakMap();
  const elementStyle = element => {
    if (!styles.has(element)) styles.set(element, getComputedStyle(element));
    return styles.get(element);
  };
  while (node) {
    if (++nodes > 4096) return blocked('snapshot_too_large');
    let depth = 0, parent = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    let visible = true, heading = false;
    for (let ancestor = parent; ancestor; ancestor = ancestor.parentElement) {
      if (++depth > 48) return blocked('snapshot_too_large');
      const style = elementStyle(ancestor);
      if (ignored.has(ancestor.tagName) || ancestor.hidden || ancestor.getAttribute('aria-hidden') === 'true'
          || style.display === 'none' || style.visibility !== 'visible' || style.opacity === '0'
          || style.contentVisibility === 'hidden') visible = false;
      if (/^H[1-6]$/.test(ancestor.tagName)) heading = true;
    }
    if (node.nodeType === Node.ELEMENT_NODE) {
      const tag = node.tagName.toUpperCase();
      if (denied.has(tag)) return blocked('sensitive_content');
      if (opaque.has(tag) || node.namespaceURI !== 'http://www.w3.org/1999/xhtml' || node.shadowRoot
          || !ordinary.has(tag) && !ignored.has(tag)) return blocked('active_content');
      if (node.hasAttribute('data-private') || node.hasAttribute('data-sensitive')
          || node.hasAttribute('data-confidential')
          || node.hasAttribute('contenteditable')) return blocked('sensitive_content');
      // Inspect bounded selected semantic labels only; never return attributes,
      // credentials, link targets, resource URLs or source paths in the view.
      for (const name of ['id','class','name','autocomplete','aria-label']) {
        const value = node.getAttribute(name);
        if (value !== null && value.length > 1024) return blocked('snapshot_too_large');
        if (value !== null && sensitive.test(value)) return blocked('sensitive_content');
      }
    } else if (visible && node.nodeValue && parent) {
      // innerText on a whole subtree would expose an unbounded intermediate.
      // Each candidate text node and ancestor chain is bounded independently.
      if (node.nodeValue.length > 2048) return blocked('snapshot_too_large');
      const range = document.createRange(); range.selectNodeContents(node);
      const inView = Array.from(range.getClientRects()).some(rect =>
        rect.width > 0 && rect.height > 0 && rect.right > 0 && rect.bottom > 0
        && rect.left < innerWidth && rect.top < innerHeight);
      if (inView) {
        const text = node.nodeValue.replace(/\s+/g,' ').trim().normalize('NFC');
        if (text) {
          if (text.length > 500) return blocked('snapshot_too_large');
          if (/[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/.test(text))
            return blocked('snapshot_invalid');
          chars += text.length; bytes += encoder.encode(text).length;
          if (blocks.length >= 128 || chars > 8192 || bytes > 32768) return blocked('snapshot_too_large');
          blocks.push({kind:heading ? 'heading' : 'text',text});
        }
      }
    }
    node = walker.nextNode();
  }
  return blocks.length ? {schema:'ananta.browser-public-view.v1',state:'ready',reason:null,blocks}
    : blocked('no_visible_text');
}"""


class PublicDocumentSnapshot:
    def __init__(self, page):
        self.page = page

    def read(self):
        try:
            # Page.evaluate does not honor page default timeouts and can wait
            # indefinitely for a new execution context after a crash race.
            # A bounded wait covers context acquisition as well as execution.
            handle = self.page.wait_for_function(SNAPSHOT_SCRIPT, timeout=750)
            try:
                value = handle.json_value()
            finally:
                handle.dispose()
        except Exception:
            return blocked_view("source_unavailable")
        try:
            return validate_public_view(value)
        except (ValueError, TypeError, RecursionError):
            return blocked_view("snapshot_invalid")
