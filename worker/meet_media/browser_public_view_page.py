"""Trusted offline renderer program. Untrusted text is never parsed as HTML."""

DOCUMENT = """<!doctype html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline';
style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'; frame-src 'none'">
<style>
html,body{margin:0;width:640px;height:360px;overflow:hidden;background:#f5f7ff;color:#161e30}
body{font:16px sans-serif}header{height:42px;background:#11284c;color:white;padding:10px 16px;box-sizing:border-box}
main{padding:8px 16px;height:276px;overflow:hidden;unicode-bidi:isolate}
p,h2{margin:4px 0;overflow-wrap:anywhere;white-space:pre-wrap}h2{font-size:19px}
footer{position:absolute;left:0;bottom:0;height:32px;width:640px;background:#11284c;color:white;font-size:12px}
#pulse{position:absolute;top:20px;left:0;width:20px;height:8px;background:#00bda5}
</style></head><body>
<header>ANANTA · KI · Bereinigte Browseransicht</header><main id="view"></main>
<footer>Öffentliche Textansicht · keine vollständige Seitendarstellung<div id="pulse"></div></footer>
<script>
window.__publicViewValid=true;
addEventListener('resize',()=>{window.__publicViewValid=false});
let pulse=0;setInterval(()=>{document.getElementById('pulse').style.left=((++pulse%30)*20)+'px'},200);
</script></body></html>"""

RENDER = """blocks => {
  if (!window.__publicViewValid || innerWidth !== 640 || innerHeight !== 360)
    throw new Error('browser_view_invalid');
  const fragment = document.createDocumentFragment();
  for (const block of blocks) {
    const node = document.createElement(block.kind === 'heading' ? 'h2' : 'p');
    node.textContent = block.text;
    fragment.appendChild(node);
  }
  document.getElementById('view').replaceChildren(fragment);
}"""

CHECK = """() => window.__publicViewValid === true && innerWidth === 640 && innerHeight === 360
  && document.querySelectorAll('header,main,footer').length === 3
  && !document.querySelector('input,textarea,form,iframe,canvas,video,audio,img,object,embed,svg')"""
