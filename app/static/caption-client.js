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

    // During continuous speech, new finals arrive faster than holdMs apart,
    // so every visible slot is always occupied by a line younger than
    // holdMs -- meaning it never actually reaches the fade window, just
    // sits fully solid the whole time. That reads as captions "taking over
    // the screen" and never clearing. Fix: once a newer final is already
    // queued to take a line's slot, that line is on its way out regardless
    // -- fade it out quickly (no hold, short fade) instead of waiting for
    // its own full hold timer, which it will likely never reach anyway.
    const FAST_FADE_MS = 500;

    function opacityFor(ts, hold, fade) {
      const age = Date.now() - ts;
      if (age <= hold) return 1;
      return Math.max(0, 1 - (age - hold) / fade);
    }

    // Overlay sits over live video, so it never gets the audience screen's
    // luxury of holding 3 lines -- it's a scrolling ticker capped at 2, and
    // unlike the audience screen, the older of those 2 lines starts fading
    // the moment a 2nd line lands, not only once a 3rd is already queued
    // behind it. Otherwise a line can sit at full opacity for its whole
    // hold_seconds while the next one is already spoken, which is what reads
    // as captions "piling up into a paragraph."
    const surfaceCap = surface === "overlay" ? 2 : 3;
    const scrollMode = surface === "overlay";

    function render() {
      pruneExpired();
      // Hard ceiling regardless of configured appearance settings -- keeps
      // captions from ever stacking past a readable amount on screen.
      const maxLines = Math.min(surfaceCap, parseInt(els.box.dataset.maxLines || String(surfaceCap), 10));
      const keep = Math.max(0, maxLines - (partial ? 1 : 0));
      // finals.slice(-0) is slice(0) in JS (whole array) -- guard the zero case explicitly.
      const shown = keep > 0 ? finals.slice(-keep) : [];
      const queuedBeyond = finals.length > shown.length;
      let html = shown
        .map((f, i) => {
          const isNewest = i === shown.length - 1;
          // Ticker mode: any line that isn't the newest already has
          // something newer showing above/after it, so it's on its way out
          // regardless of its own age. Non-ticker (audience): only the
          // oldest visible line fast-fades, and only once a newer final is
          // already queued beyond what's shown.
          const displaced = scrollMode ? !isNewest : (i === 0 && queuedBeyond);
          const hold = displaced ? 0 : holdMs;
          const fade = displaced ? FAST_FADE_MS : fadeMs;
          return "<div class=\"line\" style=\"opacity:" + opacityFor(f.ts, hold, fade) + "\">" + escapeHtml(f.text) + "</div>";
        })
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

    // The engine can emit a revised partial hypothesis every ~60ms while
    // someone is talking (see CHUNK_SECONDS in asr_engine.py) -- rendering
    // every single one is too fast to read and reads as "jumpy" even
    // though each individual change is small. Trailing-edge throttle: skip
    // renders that land too soon after the last one, but always schedule a
    // final render for the latest text so nothing is ever dropped, just
    // paced out. The operator's own live preview (control.html) is
    // unaffected -- it renders on every message, since immediacy there
    // matters more than smoothness.
    const MIN_PARTIAL_RENDER_MS = 180;
    let lastPartialRenderAt = 0;
    let partialRenderTimer = null;

    function scheduleRender() {
      const elapsed = Date.now() - lastPartialRenderAt;
      if (elapsed >= MIN_PARTIAL_RENDER_MS) {
        lastPartialRenderAt = Date.now();
        render();
        return;
      }
      if (partialRenderTimer) return;
      partialRenderTimer = setTimeout(() => {
        partialRenderTimer = null;
        lastPartialRenderAt = Date.now();
        render();
      }, MIN_PARTIAL_RENDER_MS - elapsed);
    }

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
            scheduleRender();
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
