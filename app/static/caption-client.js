// Shared by /audience and /overlay. Renders rolling captions and applies
// live appearance settings pushed from the server. Auto-reconnects so a
// browser refresh or a brief network drop recovers on its own.
(function () {
  function connect(surface, els) {
    let nextId = 0;
    const finals = []; // {id, text, ts}
    let partial = "";
    let holdMs = 8000;
    let fadeMs = 1500;

    function pushFinal(text, ts) {
      finals.push({ id: nextId++, text, ts });
    }

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

    // A "final" is whatever the recognizer had built up since the last
    // pause -- during fluent, uninterrupted speech (someone reading a
    // prepared talk) that can be a long run-on sentence, which without this
    // would just wrap inside its one line div into as many rows as it takes,
    // regardless of the line cap above. Measure against the box's actual
    // rendered width and split at word boundaries so each entry in `finals`
    // is always one visual row, matching what max-lines is meant to mean.
    let _measureCtx = null;
    function measureCtx() {
      if (!_measureCtx) _measureCtx = document.createElement("canvas").getContext("2d");
      return _measureCtx;
    }

    function boxTextMetrics() {
      const cs = getComputedStyle(els.box);
      // .line's own left+right padding (0.4em each side) eats into the
      // width text can actually use before wrapping.
      const hPadding = parseFloat(cs.fontSize) * 0.8;
      return {
        font: cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily,
        width: Math.max(50, els.box.clientWidth - hPadding),
      };
    }

    function wrapToLines(text) {
      const { font, width } = boxTextMetrics();
      const ctx = measureCtx();
      ctx.font = font;
      const words = text.split(/\s+/).filter(Boolean);
      const lines = [];
      let cur = "";
      for (const w of words) {
        const test = cur ? cur + " " + w : w;
        if (cur && ctx.measureText(test).width > width) {
          lines.push(cur);
          cur = w;
        } else {
          cur = test;
        }
      }
      if (cur) lines.push(cur);
      return lines.length ? lines : [text];
    }

    // The partial (still-being-spoken) line only ever gets 1 slot -- if it's
    // grown past one visual row, show the tail end (the words just spoken),
    // same as a live ticker rolling forward rather than backward.
    function tailLine(text) {
      if (!text) return text;
      const lines = wrapToLines(text);
      return lines[lines.length - 1];
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
    // luxury of holding 3 lines -- it's a compact ticker capped at 2, and
    // the older of those 2 lines starts fading the moment a 2nd line lands,
    // not only once a 3rd is already queued behind it. The audience screen
    // is treated completely separately (see renderScroll below): a real
    // scroll-up-and-fade motion, not an in-place opacity swap.
    const surfaceCap = surface === "overlay" ? 2 : 3;
    const isOverlay = surface === "overlay";

    function escapeHtml(text) {
      const d = document.createElement("div");
      d.textContent = text;
      return d.innerHTML;
    }

    // ---- overlay: compact ticker, rebuilt from scratch every tick --------
    function renderTicker() {
      pruneExpired();
      const maxLines = Math.min(surfaceCap, parseInt(els.box.dataset.maxLines || String(surfaceCap), 10));
      const keep = Math.max(0, maxLines - (partial ? 1 : 0));
      // finals.slice(-0) is slice(0) in JS (whole array) -- guard the zero case explicitly.
      const shown = keep > 0 ? finals.slice(-keep) : [];
      const queuedBeyond = finals.length > shown.length;
      let html = shown
        .map((f, i) => {
          const isNewest = i === shown.length - 1;
          // Any line that isn't the newest already has something newer
          // showing after it, so it's on its way out regardless of its own age.
          const displaced = !isNewest;
          const hold = displaced ? 0 : holdMs;
          const fade = displaced ? FAST_FADE_MS : fadeMs;
          return "<div class=\"line\" style=\"opacity:" + opacityFor(f.ts, hold, fade) + "\">" + escapeHtml(f.text) + "</div>";
        })
        .join("");
      if (partial) html += "<div class=\"line\">" + escapeHtml(partial) + "</div>";
      els.box.innerHTML = html;
    }

    // ---- audience: genuine scroll-up-and-fade -----------------------------
    // Unlike the overlay ticker, this keeps one persistent DOM element per
    // caption (keyed by id) instead of rebuilding the box from scratch every
    // tick -- rebuilding from scratch gives the browser nothing to animate
    // between, which is why captions read as "stuck" rather than advancing.
    // When a line is bumped out of the visible window by a newer one, it's
    // switched to position:absolute at its current on-screen spot (so
    // removing it from the flow doesn't disturb the lines still showing,
    // which -- anchored to the bottom by default -- naturally don't need to
    // move at all) and animated floating up by its own height while fading,
    // like it's scrolling off the top of the screen.
    const EXIT_MS = 700;
    const lineEls = new Map(); // id -> element
    let partialEl = null;

    function exitLine(id, el) {
      lineEls.delete(id);
      const top = el.offsetTop, left = el.offsetLeft, width = el.offsetWidth, height = el.offsetHeight;
      el.style.transition = "none";
      el.style.position = "absolute";
      el.style.top = top + "px";
      el.style.left = left + "px";
      el.style.width = width + "px";
      el.style.margin = "0";
      // Force the browser to commit the position:absolute placement above
      // before changing opacity/transform below, or it can coalesce both
      // into one paint and skip the animation entirely. requestAnimationFrame
      // would normally do this, but it's throttled/skipped for a tab that
      // isn't focused or visible (background window, some capture setups) --
      // reading a layout property forces a synchronous flush that works
      // regardless of tab visibility.
      void el.offsetHeight;
      el.style.transition = "opacity " + EXIT_MS + "ms ease, transform " + EXIT_MS + "ms ease";
      el.style.opacity = "0";
      el.style.transform = "translateY(-" + height + "px)";
      setTimeout(() => el.remove(), EXIT_MS + 150);
    }

    function renderScroll() {
      pruneExpired();
      const maxLines = Math.min(surfaceCap, parseInt(els.box.dataset.maxLines || String(surfaceCap), 10));
      const keep = Math.max(0, maxLines - (partial ? 1 : 0));
      const shown = keep > 0 ? finals.slice(-keep) : [];
      const shownIds = new Set(shown.map((f) => f.id));

      for (const [id, el] of Array.from(lineEls.entries())) {
        if (!shownIds.has(id)) exitLine(id, el);
      }

      shown.forEach((f) => {
        let el = lineEls.get(f.id);
        if (!el) {
          el = document.createElement("div");
          el.className = "line";
          el.textContent = f.text;
          el.style.transition = "none";
          el.style.opacity = "0";
          els.box.appendChild(el);
          lineEls.set(f.id, el);
          void el.offsetHeight; // see exitLine -- same forced-flush reasoning, same rAF-throttling risk
          el.style.transition = "opacity 0.4s ease";
          el.style.opacity = String(opacityFor(f.ts, holdMs, fadeMs));
          return;
        }
        el.textContent = f.text;
        el.style.opacity = String(opacityFor(f.ts, holdMs, fadeMs));
      });

      if (partial) {
        if (!partialEl) {
          partialEl = document.createElement("div");
          partialEl.className = "line";
          els.box.appendChild(partialEl);
        }
        partialEl.textContent = partial;
        partialEl.style.opacity = "1";
      } else if (partialEl) {
        partialEl.remove();
        partialEl = null;
      }
    }

    function resetScrollDom() {
      for (const el of lineEls.values()) el.remove();
      lineEls.clear();
      if (partialEl) {
        partialEl.remove();
        partialEl = null;
      }
    }

    const render = isOverlay ? renderTicker : renderScroll;

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

    // Rows of the current in-progress utterance already promoted from
    // `partial` into `finals`. Fast, fluent speech with no pauses may go a
    // long time between true "final" events (the recognizer only finalizes
    // on a trailing silence gap) -- without this, the display would just
    // sit showing one ever-growing partial line, never advancing to a 2nd
    // line, however fast someone talks. Promoting each row the instant it's
    // full (independent of when the recognizer decides the sentence ended)
    // makes the display advance on screen width alone, same as a real
    // scrolling subtitle.
    let committedPartialRows = 0;

    function promotePartialRows(text, keepLastAsPartial) {
      const lines = wrapToLines(text);
      const upTo = keepLastAsPartial ? Math.max(committedPartialRows, lines.length - 1) : lines.length;
      const newlyCompleted = lines.slice(committedPartialRows, upTo);
      const now = Date.now();
      newlyCompleted.forEach((line) => pushFinal(line, now));
      committedPartialRows += newlyCompleted.length;
      return keepLastAsPartial ? lines[lines.length - 1] || "" : "";
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
            committedPartialRows = 0;
            if (!isOverlay) resetScrollDom();
            const now = Date.now();
            msg.recent_finals.forEach((text) => {
              wrapToLines(text).forEach((line) => pushFinal(line, now));
            });
            partial = tailLine(msg.partial || "");
            render();
            break;
          case "partial":
            partial = promotePartialRows(msg.text, true);
            scheduleRender();
            break;
          case "final":
            promotePartialRows(msg.text, false);
            committedPartialRows = 0;
            partial = "";
            render();
            break;
          case "clear":
            finals.length = 0;
            committedPartialRows = 0;
            if (!isOverlay) resetScrollDom();
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
