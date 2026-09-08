// Shared by /audience and /overlay. Renders rolling captions and applies
// live appearance settings pushed from the server. Auto-reconnects so a
// browser refresh or a brief network drop recovers on its own.
(function () {
  function connect(surface, els) {
    const finals = []; // {text, ts}
    let partial = "";
    let holdMs = 8000;
    let fadeMs = 1500;

    function applyAppearance(appearance) {
      const cfg = appearance[surface];
      if (!cfg) return;
      const box = els.box;
      box.style.fontFamily = cfg.font_family;
      box.style.fontSize = cfg.font_size + "px";
      box.style.fontWeight = cfg.font_weight;
      box.style.textAlign = cfg.text_align;
      box.style.lineHeight = cfg.line_height;
      box.style.color = cfg.text_color;
      box.style.setProperty("--bg-color", cfg.background_color);
      box.style.setProperty("--bg-opacity", cfg.background_opacity);
      box.dataset.maxLines = cfg.max_lines;
      box.dataset.position = cfg.position;
      els.wrap.dataset.position = cfg.position;
      holdMs = (cfg.hold_seconds || 8) * 1000;
      fadeMs = (cfg.fade_seconds || 1.5) * 1000;
      render();
    }

    function pruneExpired() {
      const now = Date.now();
      for (let i = finals.length - 1; i >= 0; i--) {
        if (now - finals[i].ts > holdMs + fadeMs) finals.splice(i, 1);
      }
    }

    function opacityFor(ts) {
      const age = Date.now() - ts;
      if (age <= holdMs) return 1;
      return Math.max(0, 1 - (age - holdMs) / fadeMs);
    }

    function render() {
      pruneExpired();
      const maxLines = parseInt(els.box.dataset.maxLines || "3", 10);
      const shown = finals.slice(-Math.max(0, maxLines - (partial ? 1 : 0)));
      let html = shown
        .map((f) => "<div class=\"line\" style=\"opacity:" + opacityFor(f.ts) + "\">" + escapeHtml(f.text) + "</div>")
        .join("");
      if (partial) html += "<div class=\"line\">" + escapeHtml(partial) + "</div>";
      els.box.innerHTML = html;
    }

    function escapeHtml(text) {
      const d = document.createElement("div");
      d.textContent = text;
      return d.innerHTML;
    }

    setInterval(render, 200);

    function open() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(proto + "://" + location.host + "/ws");

      ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);
        switch (msg.type) {
          case "appearance":
            applyAppearance(msg);
            break;
          case "sync":
            finals.length = 0;
            const now = Date.now();
            msg.recent_finals.forEach((text) => finals.push({ text, ts: now }));
            partial = msg.partial || "";
            render();
            break;
          case "partial":
            partial = msg.text;
            render();
            break;
          case "final":
            finals.push({ text: msg.text, ts: Date.now() });
            partial = "";
            render();
            break;
          case "clear":
            finals.length = 0;
            partial = "";
            render();
            break;
        }
      };

      ws.onclose = () => setTimeout(open, 1500);
      ws.onerror = () => ws.close();
    }

    open();
  }

  window.ATLiveCaptionClient = { connect };
})();
