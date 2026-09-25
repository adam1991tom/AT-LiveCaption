// Shared by /audience and /overlay. Renders rolling captions and applies
// live appearance settings pushed from the server. Auto-reconnects so a
// browser refresh or a brief network drop recovers on its own.
//
// Both surfaces use the exact same rendering mechanism -- the only
// difference between them is how many lines each is capped at (2 for
// overlay, since it sits over live video; 3 for the full-screen audience
// display) and their own independent appearance settings (font, size,
// colors, timing). An earlier version gave the audience screen a separate,
// more elaborate scrolling animation, but that turned into its own source
// of bugs and felt inconsistent with the overlay's simple, reliable
// behavior -- simplicity won.
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
      if (els.disclaimer) els.disclaimer.hidden = !cfg.show_disclaimer;
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
      // .line's own left+right padding eats into the width text can
      // actually use before wrapping.
      const hPadding = parseFloat(cs.fontSize) * 1.2;
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

    // Not a design opinion about readability -- the operator sets max_lines
    // themselves in the Appearance tab, fully customizable up to this. This
    // is purely a sanity bound (matches the server-side one) so a stray
    // value can't leave the page trying to render something absurd.
    const MAX_LINES_SAFETY_CAP = 20;

    function escapeHtml(text) {
      const d = document.createElement("div");
      d.textContent = text;
      return d.innerHTML;
    }

    // Overlay is a tight, compact ticker (2 lines by default) -- any line
    // that isn't the newest already has something newer next to it, so it
    // reads best fading out immediately rather than holding. That rule
    // would backfire for the audience screen once max_lines is turned up
    // to fill a screen with several lines of history: fading out every
    // line except the newest would mean only ever seeing 1-2 lines
    // solid, however many the operator configured. Audience instead holds
    // every visible line for its own hold/fade time, and only fast-fades
    // the single oldest one, and only once it's actually about to be
    // pushed out by overflow (a newer line queued beyond what's shown).
    const isOverlay = surface === "overlay";

    function render() {
      pruneExpired();
      const maxLines = Math.min(MAX_LINES_SAFETY_CAP, parseInt(els.box.dataset.maxLines || "3", 10));
      const keep = Math.max(0, maxLines - (partial ? 1 : 0));
      // finals.slice(-0) is slice(0) in JS (whole array) -- guard the zero case explicitly.
      const shown = keep > 0 ? finals.slice(-keep) : [];
      const queuedBeyond = finals.length > shown.length;
      let html = shown
        .map((f, i) => {
          const isNewest = i === shown.length - 1;
          const displaced = isOverlay ? (!isNewest || (i === 0 && queuedBeyond)) : (i === 0 && queuedBeyond);
          const hold = displaced ? 0 : holdMs;
          const fade = displaced ? FAST_FADE_MS : fadeMs;
          return "<div class=\"line\" style=\"opacity:" + opacityFor(f.ts, hold, fade) + "\">" + escapeHtml(f.text) + "</div>";
        })
        .join("");
      if (partial) html += "<div class=\"line\">" + escapeHtml(partial) + "</div>";
      els.box.innerHTML = html;
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

    // Rows of the current in-progress utterance already promoted from
    // `partial` into `finals`. Fast, fluent speech with no pauses may go a
    // long time between true "final" events (the recognizer only finalizes
    // on a trailing silence gap) -- without this, the display would just
    // sit showing one ever-growing partial line, never advancing to a 2nd
    // line, however fast someone talks. Promoting each row the instant it's
    // full (independent of when the recognizer decides the sentence ended)
    // makes the ticker advance on screen width alone, same as a real
    // scrolling subtitle.
    let committedPartialRows = 0;

    function promotePartialRows(text, keepLastAsPartial) {
      const lines = wrapToLines(text);
      const upTo = keepLastAsPartial ? Math.max(committedPartialRows, lines.length - 1) : lines.length;
      const newlyCompleted = lines.slice(committedPartialRows, upTo);
      newlyCompleted.forEach((line) => finals.push({ text: line, ts: Date.now() }));
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
            const now = Date.now();
            msg.recent_finals.forEach((text) => {
              wrapToLines(text).forEach((line) => finals.push({ text: line, ts: now }));
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
