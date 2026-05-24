(() => {
  const DEFAULT_CAST_URL = "https://raw.githubusercontent.com/ananta888/ananta/main/tests/output/operator_tui_splash.cast";

  const ANSI_256 = [
    [0, 0, 0], [128, 0, 0], [0, 128, 0], [128, 128, 0], [0, 0, 128], [128, 0, 128], [0, 128, 128], [192, 192, 192],
    [128, 128, 128], [255, 0, 0], [0, 255, 0], [255, 255, 0], [0, 0, 255], [255, 0, 255], [0, 255, 255], [255, 255, 255]
  ];

  for (let r = 0; r < 6; r += 1) {
    for (let g = 0; g < 6; g += 1) {
      for (let b = 0; b < 6; b += 1) {
        ANSI_256.push([r ? r * 40 + 55 : 0, g ? g * 40 + 55 : 0, b ? b * 40 + 55 : 0]);
      }
    }
  }

  for (let i = 0; i < 24; i += 1) {
    const level = 8 + i * 10;
    ANSI_256.push([level, level, level]);
  }

  function escapeHtml(text) {
    return text
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function ansiToHtml(input) {
    let html = "";
    let open = false;
    let pos = 0;
    const regex = /\x1b\[([0-9;]*)m/g;
    let match;

    while ((match = regex.exec(input)) !== null) {
      html += escapeHtml(input.slice(pos, match.index));
      pos = regex.lastIndex;
      const codes = (match[1] || "0").split(";").filter(Boolean).map(Number);

      for (let i = 0; i < codes.length; i += 1) {
        const code = codes[i];
        if (code === 0) {
          if (open) html += "</span>";
          open = false;
        } else if (code === 38 && codes[i + 1] === 2) {
          const r = codes[i + 2] ?? 112;
          const g = codes[i + 3] ?? 225;
          const b = codes[i + 4] ?? 200;
          if (open) html += "</span>";
          html += `<span style="color: rgb(${r}, ${g}, ${b})">`;
          open = true;
          i += 4;
        } else if (code === 38 && codes[i + 1] === 5) {
          const [r, g, b] = ANSI_256[codes[i + 2] ?? 14] || ANSI_256[14];
          if (open) html += "</span>";
          html += `<span style="color: rgb(${r}, ${g}, ${b})">`;
          open = true;
          i += 2;
        }
      }
    }

    html += escapeHtml(input.slice(pos));
    if (open) html += "</span>";
    return html;
  }

  function cleanFrame(frame) {
    return frame
      .replaceAll("\x1b[H", "")
      .replaceAll("\x1b[2J", "")
      .replaceAll("\x1b[?25l", "")
      .replaceAll("\x1b[?25h", "");
  }

  async function loadCast(url) {
    const res = await fetch(url, { cache: "force-cache" });
    if (!res.ok) throw new Error(`cast fetch failed: ${res.status}`);
    const text = await res.text();
    return text
      .trim()
      .split(/\n+/)
      .slice(1)
      .map((line) => JSON.parse(line))
      .filter((event) => event[1] === "o" && typeof event[2] === "string")
      .map((event) => ({ time: Number(event[0]) || 0, html: ansiToHtml(cleanFrame(event[2])) }));
  }

  async function mountTerminalCastPlayer(node) {
    const output = node.querySelector("[data-terminal-output]");
    const status = node.querySelector("[data-terminal-status]");
    const url = node.dataset.castUrl || DEFAULT_CAST_URL;

    try {
      if (status) status.textContent = "loading cast";
      const frames = await loadCast(url);
      if (!frames.length) throw new Error("no frames");
      if (status) status.textContent = `${frames.length} frames · asciinema v2`;

      let index = 0;
      const render = () => {
        output.innerHTML = frames[index].html;
        const current = frames[index];
        const next = frames[(index + 1) % frames.length];
        const delay = Math.max(24, Math.min(140, ((next.time - current.time) || 0.0417) * 1000));
        index = (index + 1) % frames.length;
        window.setTimeout(render, delay);
      };

      render();
    } catch (error) {
      if (status) status.textContent = "fallback animation";
      output.textContent = [
        "        /\\        ",
        "       /  \\       ",
        "      / /\\ \\      ",
        "     / ____ \\     ",
        "    /_/    \\_\\    ",
        "       ~ ananta ~   ",
        "  agent control hub ",
      ].join("\n");
      console.warn("Ananta terminal cast player fallback:", error);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-terminal-cast]").forEach(mountTerminalCastPlayer);
  });
})();
