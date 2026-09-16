"""Rasterize ananta.svg into a normalized RGBA PNG for the machine avatar."""

import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

SIZE = 512
src = Path("/state/ananta.svg").read_text(encoding="utf-8")
html = (
    "<!doctype html><html><head><style>html,body{margin:0;padding:0;background:transparent}"
    "svg{display:block;width:%dpx;height:%dpx}</style></head><body>%s</body></html>" % (SIZE, SIZE, src)
)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, chromium_sandbox=True)
    page = browser.new_page(viewport={"width": SIZE, "height": SIZE}, device_scale_factor=1)
    page.set_content(html, wait_until="load")
    page.wait_for_timeout(300)
    png = page.screenshot(clip={"x": 0, "y": 0, "width": SIZE, "height": SIZE}, omit_background=True)
    browser.close()

image = Image.open(BytesIO(png)).convert("RGBA")
buffer = BytesIO()
image.save(buffer, format="PNG")
png = buffer.getvalue()
Path("/state/ananta-avatar.png").write_bytes(png)
print("bytes=%d sha256=%s" % (len(png), hashlib.sha256(png).hexdigest()))
print("png_color_type=%d width=%d height=%d" % (
    png[25], int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")))