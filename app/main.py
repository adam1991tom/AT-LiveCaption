"""AT LiveCaption control server.

Serves the control page (/), audience screen (/audience) and camera overlay
(/overlay), and fans out live captions over a single WebSocket (/ws).
Everything runs locally: no audio ever leaves this process, and nothing is
sent to any external service.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.core import audio_devices, corrections, hotwords, model_download, network_info, resources, update_check
from app.core.asr_engine import CaptionEngine
from app.core.config import DATA_DIR, THEMES, load_config, save_config
from app.core.hub import ConnectionHub
from app.core.procutil import CREATIONFLAGS, get_port
from app.core.transcript import TranscriptWriter, to_srt, to_vtt
from app import version

HOTWORDS_PATH = DATA_DIR / "hotwords.txt"
CORRECTIONS_PATH = DATA_DIR / "corrections.json"

# Not a design opinion about how many lines is readable -- just a sanity
# bound so a stray value (an accidental extra zero, a bad paste) can't
# leave the app trying to render something absurd. The operator is fully
# in control of max_lines up to this within the Appearance tab.
MAX_LINES_SAFETY_CAP = 20

STATIC_DIR = Path(__file__).parent / "static"

# The server binds 0.0.0.0 (see server_main.py) so the audience screen and
# camera overlay can be opened from another device on the same network --
# a phone, or a separate PC driving a projector/TV. But that means, without
# this, EVERY route including the full control panel, the about/GDPR page,
# and every /api/* call (change the mic, edit vocabulary, download
# transcripts) would also be reachable, unauthenticated, by anyone else on
# that network. Only the exact handful of routes those two read-only
# display pages need (their own page, the shared static assets, and the
# broadcast-only websocket) are allowed from off-machine; everything else
# needs either to be this same machine, or a device the operator has
# approved through the pairing flow below (config["trusted_control_devices"])
# -- an allowlist instead of a password, for a second person (e.g. a venue
# tech) who needs the full control panel from their own laptop.
_LOCAL_HOSTS = {"127.0.0.1", "::1"}
_LAN_ALLOWED_EXACT = {"/audience", "/overlay", "/ws", "/request-access"}
_LAN_ALLOWED_PREFIXES = ("/static/",)

# Pairing flow: an untrusted device that lands on "/" is bounced to
# /request-access, which asks it to enter a code -- and also pings the admin
# (POST /api/pairing-code/announce, a websocket "pairing_requested" message)
# so a code appears on the control panel automatically instead of the admin
# having to remember to generate one ahead of time. The admin can also
# generate one proactively from the Access tab. Either way, the code shows
# up only on the control panel, to be read out to whoever needs it; entering
# it correctly (POST /api/pairing-redeem, along with the announce call, the
# only calls an untrusted device is allowed to make on its own behalf)
# trusts that device's address. One code is live at a time, time-boxed, and
# auto-invalidated after too many wrong guesses -- the pairing-redeem
# endpoint has to be reachable by definition by a device we don't trust yet,
# so the attempt cap is what stands in for rate-limiting against someone
# just trying every 4-digit combination.
_PAIRING_CODE_TTL_SECONDS = 600
_PAIRING_CODE_MAX_ATTEMPTS = 10
_active_pairing_code: dict | None = None


def _current_pairing_code() -> dict | None:
    global _active_pairing_code
    if _active_pairing_code is None:
        return None
    if time.time() - _active_pairing_code["created_at"] > _PAIRING_CODE_TTL_SECONDS:
        _active_pairing_code = None
        return None
    return _active_pairing_code


def _is_trusted_control_client(client_host: str | None, config: dict) -> bool:
    if client_host in _LOCAL_HOSTS:
        return True
    if not client_host:
        return False
    try:
        client_ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    for entry in config.get("trusted_control_devices", []):
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if client_ip in network:
            return True
    return False


def _is_public_pairing_call(method: str, path: str) -> bool:
    """The only calls an untrusted device is allowed to make on its own
    behalf: say "someone's here" (no code involved) and submit a code to
    try to become trusted."""
    if method != "POST":
        return False
    return path in ("/api/pairing-redeem", "/api/pairing-code/announce")


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _LAN_ALLOWED_EXACT or path.startswith(_LAN_ALLOWED_PREFIXES):
            return await call_next(request)
        if _is_public_pairing_call(request.method, path):
            return await call_next(request)
        client_host = request.client.host if request.client else None
        if not _is_trusted_control_client(client_host, state["config"]):
            if request.method == "GET" and path == "/":
                # A human just typed/clicked their way to the control panel
                # from an unrecognized device -- send them to the pairing
                # page instead of a bare "Forbidden".
                return RedirectResponse("/request-access")
            return PlainTextResponse(
                "Forbidden: this device isn't trusted to control AT LiveCaption yet. "
                "Go to / on this device to request access.",
                status_code=403,
            )
        return await call_next(request)


_NO_CACHE_PAGES = {"/", "/audience", "/overlay", "/about", "/request-access"}


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Static files and the pages that load them ship with an ETag/
    Last-Modified but no Cache-Control, so browsers apply their own
    heuristic freshness lifetime and can go on serving a stale page or
    script for a while after an update even across a normal refresh --
    only a hard reload reliably bypasses it. The page routes themselves
    (control.html, audience.html, overlay.html, ...) need this just as
    much as /static/*: each one carries its own inline script, so a
    cached copy of the PAGE is just as stale as a cached copy of a script
    file would be. Forcing revalidation means every request still
    round-trips (cheap: a 304 when nothing changed), but a genuine update
    is never missed."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/") or request.url.path in _NO_CACHE_PAGES:
            response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title="AT LiveCaption")
app.add_middleware(NoCacheStaticMiddleware)
app.add_middleware(LocalOnlyMiddleware)
hub = ConnectionHub()

state: dict = {"config": load_config(), "engine": None, "model_state": "not_loaded"}


def _appearance_message() -> dict:
    return {
        "type": "appearance",
        "theme": state["config"]["theme"],
        **state["config"]["appearance"],
    }


def _broadcast_engine_status(ready: bool, engine_state: str, progress: float | None = None) -> None:
    """Single place that builds the engine-loading status message, used by
    every stage of model prep so the shape can't drift between call sites."""
    engine_payload: dict = {"ready": ready, "state": engine_state}
    if progress is not None:
        engine_payload["progress"] = progress
    asyncio.run_coroutine_threadsafe(
        hub.broadcast(
            {
                "type": "status",
                "audio": hub.status.get("audio", {}),
                "engine": engine_payload,
            }
        ),
        state["loop"],
    )


@app.on_event("startup")
async def startup() -> None:
    state["loop"] = asyncio.get_event_loop()
    config = state["config"]

    transcript_writer = TranscriptWriter(config["transcript_dir"], config["save_transcript"])
    engine = CaptionEngine(state["loop"], hub, transcript_writer)
    engine.set_gain_db(config["audio_gain_db"])
    for i, gain_db in enumerate(config["eq_band_gains_db"]):
        engine.set_band_gain_db(i, gain_db)
    state["engine"] = engine
    state["transcript_writer"] = transcript_writer

    await hub.broadcast(_appearance_message())

    resources.prime()

    async def _broadcast_resources_forever():
        loop = asyncio.get_event_loop()
        while True:
            if hub.client_count() > 0:
                stats = await loop.run_in_executor(None, resources.get_resource_stats)
                await hub.broadcast({"type": "resources", **stats})
            await asyncio.sleep(2.0)

    asyncio.create_task(_broadcast_resources_forever())

    def _prepare_model_and_maybe_start():
        model_dir = Path(config["model_dir"])
        state["model_state"] = "checking"

        if not model_download.model_is_ready(model_dir):
            state["model_state"] = "downloading"

            def _progress(stage, pct):
                state["model_state"] = stage
                _broadcast_engine_status(ready=False, engine_state=f"model_{stage}", progress=pct)

            try:
                model_download.download_and_extract(model_dir, progress_cb=_progress)
            except Exception as exc:
                state["model_state"] = f"error: {exc}"
                _broadcast_engine_status(ready=False, engine_state="model_error")
                return

        hotwords.build_hotwords_file(
            config.get("vocabulary", []), str(model_dir / "tokens.txt"), str(HOTWORDS_PATH)
        )
        try:
            engine.load_model(
                str(model_dir),
                hotwords_file=str(HOTWORDS_PATH),
                hotwords_score=config.get("hotwords_score", 2.5),
            )
        except Exception as exc:
            state["model_state"] = f"error: {exc}"
            _broadcast_engine_status(ready=False, engine_state="model_error")
            return

        state["model_state"] = "ready"
        _broadcast_engine_status(ready=True, engine_state="ready")

        device_index = config.get("audio_device_index")
        device_name = config.get("audio_device_name")
        if device_index is not None and audio_devices.device_exists(device_index):
            engine.start(device_index, device_name or "")

    threading.Thread(target=_prepare_model_and_maybe_start, daemon=True).start()


@app.on_event("shutdown")
async def shutdown() -> None:
    engine: CaptionEngine = state["engine"]
    if engine:
        engine.stop()


# ---- native window launcher -------------------------------------------
# A plain <a target="_blank"> works fine in a real browser tab, but the
# control panel is often viewed inside its own native window (WebView2, via
# app/window.py) -- an embedded browser shell with no concept of "open a
# new tab", so target="_blank" there silently does nothing. Opening the
# Audience/Overlay screens as their own native windows means spawning a
# whole new OS process (mirrors app/tray.py's own menu items exactly, right
# down to reusing the same instance lock so a 2nd click focuses the
# existing window instead of opening a duplicate) -- something no
# client-side link can do on its own, so the control page calls this
# instead when it detects it's running inside that shell (see control.html).
def _window_argv(page: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, f"--window={page}"]
    entry = Path(__file__).resolve().parent.parent / "app_entry.py"
    return [sys.executable, str(entry), f"--window={page}"]


@app.post("/api/open-window")
async def api_open_window(payload: dict):
    page = payload.get("page")
    if page not in ("control", "audience", "overlay"):
        return JSONResponse({"error": "unknown page"}, status_code=400)
    subprocess.Popen(_window_argv(page), creationflags=CREATIONFLAGS)
    return JSONResponse({"ok": True})


# ---- pages -----------------------------------------------------------------
@app.get("/")
async def control_page():
    return FileResponse(STATIC_DIR / "control.html")


@app.get("/audience")
async def audience_page():
    return FileResponse(STATIC_DIR / "audience.html")


@app.get("/overlay")
async def overlay_page():
    return FileResponse(STATIC_DIR / "overlay.html")


@app.get("/request-access")
async def request_access_page():
    return FileResponse(STATIC_DIR / "request_access.html")


@app.get("/about")
async def about_page():
    return FileResponse(STATIC_DIR / "about.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---- websocket ---------------------------------------------------------
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # clients don't send anything meaningful yet
    except WebSocketDisconnect:
        await hub.disconnect(websocket)


# ---- REST API ------------------------------------------------------------
@app.get("/api/about")
async def api_about():
    config = state["config"]
    # The active model can now differ from this build's own bundled
    # default -- the background model-quality check (update_check.py /
    # model_bench.py) can auto-promote a different, better-tested one --
    # so this reflects config.model_dir's own name, not the fixed constant.
    return JSONResponse(
        {
            "app_version": version.APP_VERSION,
            "asr_engine": version.ASR_ENGINE,
            "model_name": Path(config["model_dir"]).name,
            "model_source_url": version.MODEL_SOURCE_URL,
            "model_can_revert": bool(config.get("previous_model_dir")),
            "save_transcript": config["save_transcript"],
            "transcript_dir": config["transcript_dir"],
        }
    )


@app.get("/api/devices")
async def api_devices():
    return JSONResponse(audio_devices.list_input_devices())


@app.post("/api/devices/rescan")
async def api_devices_rescan():
    engine: CaptionEngine = state["engine"]
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, engine.refresh_devices)
    return JSONResponse(audio_devices.list_input_devices())


@app.get("/api/status")
async def api_status():
    return JSONResponse(
        {
            "audio": hub.status.get("audio", {}),
            "engine": hub.status.get("engine", {}),
            "model_state": state["model_state"],
            "clients": hub.client_count(),
        }
    )


@app.post("/api/devices/select")
async def api_select_device(payload: dict):
    index = payload.get("index")
    devices = {d["index"]: d["name"] for d in audio_devices.list_input_devices()}
    if index not in devices:
        return JSONResponse({"error": "unknown device index"}, status_code=400)

    config = state["config"]
    config["audio_device_index"] = index
    config["audio_device_name"] = devices[index]
    save_config(config)

    engine: CaptionEngine = state["engine"]
    if state["model_state"] == "ready":
        engine.start(index, devices[index])
    return JSONResponse({"ok": True, "index": index, "name": devices[index]})


@app.get("/api/audio/dsp")
async def api_get_dsp():
    engine: CaptionEngine = state["engine"]
    return JSONResponse(engine.audio_settings())


@app.post("/api/audio/dsp")
async def api_set_dsp(payload: dict):
    engine: CaptionEngine = state["engine"]
    config = state["config"]

    if "gain_db" in payload:
        engine.set_gain_db(float(payload["gain_db"]))

    for band in payload.get("eq_bands", []):
        engine.set_band_gain_db(int(band["index"]), float(band["gain_db"]))

    settings = engine.audio_settings()
    config["audio_gain_db"] = settings["gain_db"]
    config["eq_band_gains_db"] = settings["eq_band_gains_db"]
    # Dragging an EQ handle fires this endpoint up to ~every 80ms; a
    # blocking file write on the event loop here would stall every other
    # client's status/RTA broadcasts for the duration of the drag.
    await asyncio.get_event_loop().run_in_executor(None, save_config, config)
    return JSONResponse({"ok": True, **settings})


@app.post("/api/check-updates")
async def api_check_updates():
    current_model = Path(state["config"]["model_dir"]).name
    result = await asyncio.get_event_loop().run_in_executor(None, update_check.check_for_updates, current_model)
    return JSONResponse(result)


@app.post("/api/model/revert")
async def api_model_revert():
    """Swaps model_dir back to whatever was active before the background
    model-quality check last auto-promoted a candidate (see
    update_check.try_update_model()). Swaps rather than just clearing, so
    clicking this again "reverts the revert" back to the other one.
    """
    config = state["config"]
    previous = config.get("previous_model_dir")
    if not previous or not model_download.model_is_ready(Path(previous)):
        return JSONResponse({"error": "No previous model to revert to."}, status_code=400)

    config["previous_model_dir"], config["model_dir"] = config["model_dir"], previous
    await asyncio.get_event_loop().run_in_executor(None, save_config, config)

    # This process only reads model_dir once, at its own startup -- the
    # only way it picks up the swap is to end and let the tray's
    # supervisor restart it (same mechanism as "Restart Caption Server"),
    # so respond first and give the response a moment to actually reach
    # the browser before this process disappears.
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return JSONResponse({"ok": True, "reverted_to": Path(config["model_dir"]).name})


@app.get("/api/vocabulary")
async def api_get_vocabulary():
    config = state["config"]
    return JSONResponse({"phrases": config.get("vocabulary", []), "hotwords_score": config.get("hotwords_score", 2.5)})


@app.post("/api/vocabulary")
async def api_set_vocabulary(payload: dict):
    config = state["config"]
    phrases = [str(p).strip() for p in payload.get("phrases", []) if str(p).strip()]
    score = float(payload.get("hotwords_score", config.get("hotwords_score", 2.5)))
    config["vocabulary"] = phrases
    config["hotwords_score"] = score
    save_config(config)

    model_dir = Path(config["model_dir"])

    def _rebuild_and_reload() -> list[str]:
        skipped = hotwords.build_hotwords_file(phrases, str(model_dir / "tokens.txt"), str(HOTWORDS_PATH))
        engine: CaptionEngine = state["engine"]
        if state["model_state"] == "ready":
            # Hotwords only take effect on a freshly built recognizer, so
            # reload it in place -- load_model() restarts the audio stream
            # on the same device afterward if one was already running.
            engine.load_model(
                str(model_dir), hotwords_file=str(HOTWORDS_PATH), hotwords_score=score,
            )
        return skipped

    skipped = await asyncio.get_event_loop().run_in_executor(None, _rebuild_and_reload)
    return JSONResponse({"ok": True, "phrases": phrases, "hotwords_score": score, "skipped": skipped})


@app.get("/api/caption-history")
async def api_get_caption_history():
    return JSONResponse(hub.get_caption_history())


@app.post("/api/caption-history/clear")
async def api_clear_caption_history():
    hub.clear_caption_history()
    return JSONResponse({"ok": True})


@app.get("/api/corrections")
async def api_get_corrections():
    return JSONResponse(list(reversed(corrections.load_corrections(CORRECTIONS_PATH))))


@app.post("/api/corrections")
async def api_add_correction(payload: dict):
    original = str(payload.get("original", "")).strip()
    corrected = str(payload.get("corrected", "")).strip()
    if not original or not corrected:
        return JSONResponse({"error": "Both the original and corrected word are required."}, status_code=400)

    corrections.add_correction(CORRECTIONS_PATH, original, corrected)

    # The actual "learning" this app can do: boost the corrected word in the
    # custom vocabulary so the engine is more likely to get it right next
    # time -- not retraining the model itself, which isn't something a
    # local desktop app can do.
    config = state["config"]
    vocabulary = config.get("vocabulary", [])
    added_to_vocabulary = False
    if corrected.lower() not in [v.lower() for v in vocabulary]:
        vocabulary.append(corrected)
        config["vocabulary"] = vocabulary
        save_config(config)
        added_to_vocabulary = True

    model_dir = Path(config["model_dir"])

    def _rebuild_and_reload() -> list[str]:
        skipped = hotwords.build_hotwords_file(vocabulary, str(model_dir / "tokens.txt"), str(HOTWORDS_PATH))
        engine: CaptionEngine = state["engine"]
        # A correction is meant to be a quick, in-the-moment fix during a
        # live session, not a settings change -- unlike the Vocabulary tab's
        # explicit Save, it must never stop and restart the audio device
        # (load_model()'s reload does exactly that, and can take several
        # seconds). If the engine is actively capturing, just leave the
        # freshly-written hotwords file in place; it takes effect on the
        # next start/reload regardless, without ever interrupting the room.
        if state["model_state"] == "ready" and not engine.is_running():
            engine.load_model(
                str(model_dir), hotwords_file=str(HOTWORDS_PATH),
                hotwords_score=config.get("hotwords_score", 2.5),
            )
        return skipped

    skipped = await asyncio.get_event_loop().run_in_executor(None, _rebuild_and_reload) if added_to_vocabulary else []
    return JSONResponse({"ok": True, "added_to_vocabulary": added_to_vocabulary, "skipped": skipped})


# ---- device pairing (see LocalOnlyMiddleware above) ------------------------
@app.post("/api/pairing-code")
async def api_generate_pairing_code():
    global _active_pairing_code
    code = f"{secrets.randbelow(10000):04d}"
    _active_pairing_code = {"code": code, "created_at": time.time(), "attempts": 0}
    return JSONResponse({"code": code, "expires_in": _PAIRING_CODE_TTL_SECONDS})


@app.get("/api/pairing-code")
async def api_get_pairing_code():
    active = _current_pairing_code()
    if not active:
        return JSONResponse({"active": False})
    remaining = _PAIRING_CODE_TTL_SECONDS - (time.time() - active["created_at"])
    return JSONResponse({"active": True, "code": active["code"], "expires_in": round(remaining)})


@app.post("/api/pairing-code/disable")
async def api_disable_pairing_code():
    global _active_pairing_code
    _active_pairing_code = None
    return JSONResponse({"ok": True})


@app.post("/api/pairing-code/announce")
async def api_announce_pairing_request(request: Request):
    """Called by an untrusted device's /request-access page on load -- makes
    sure a code exists (generating one if nothing is active yet) and pings
    the control panel so a code shows up there without the admin having to
    have already clicked Generate Code themselves."""
    global _active_pairing_code
    if _current_pairing_code() is None:
        _active_pairing_code = {"code": f"{secrets.randbelow(10000):04d}", "created_at": time.time(), "attempts": 0}
    ip = request.client.host if request.client else "unknown"
    await hub.broadcast({"type": "pairing_requested", "ip": ip})
    return JSONResponse({"ok": True})


@app.post("/api/pairing-redeem")
async def api_pairing_redeem(request: Request, payload: dict):
    active = _current_pairing_code()
    if not active:
        return JSONResponse({"error": "No pairing code is active right now."}, status_code=400)
    submitted = str(payload.get("code", "")).strip()
    if submitted != active["code"]:
        active["attempts"] += 1
        if active["attempts"] >= _PAIRING_CODE_MAX_ATTEMPTS:
            global _active_pairing_code
            _active_pairing_code = None
        return JSONResponse({"error": "That code is incorrect."}, status_code=400)

    ip = request.client.host if request.client else None
    if not ip:
        return JSONResponse({"error": "Couldn't determine your device's address."}, status_code=400)
    config = state["config"]
    if ip not in config["trusted_control_devices"]:
        config["trusted_control_devices"].append(ip)
        save_config(config)
    return JSONResponse({"ok": True})


@app.get("/api/network-info")
async def api_network_info():
    return JSONResponse({"addresses": network_info.list_lan_addresses(), "port": get_port()})


@app.get("/api/trusted-devices")
async def api_get_trusted_devices():
    return JSONResponse({"devices": state["config"].get("trusted_control_devices", [])})


@app.post("/api/trusted-devices/remove")
async def api_remove_trusted_device(payload: dict):
    config = state["config"]
    entry = payload.get("device")
    devices = config.get("trusted_control_devices", [])
    if entry in devices:
        devices.remove(entry)
        save_config(config)
    return JSONResponse({"ok": True, "devices": devices})


@app.post("/api/test-partial")
async def api_test_partial(text: str):
    await hub.broadcast({"type": "partial", "text": text})
    return JSONResponse({"ok": True})


@app.post("/api/test-caption")
async def api_test_caption(text: str = "This is a test caption from AT LiveCaption."):
    words = [{"text": w, "confidence": 0.5} for w in text.split()]
    await hub.broadcast({"type": "final", "text": text, "words": words})
    return JSONResponse({"ok": True})


@app.post("/api/clear")
async def api_clear():
    await hub.broadcast({"type": "clear"})
    return JSONResponse({"ok": True})


@app.get("/api/theme")
async def api_get_theme():
    return JSONResponse({"theme": state["config"]["theme"], "themes": THEMES})


@app.post("/api/theme")
async def api_set_theme(payload: dict):
    theme = payload.get("theme")
    if theme not in THEMES:
        return JSONResponse({"error": "unknown theme"}, status_code=400)
    config = state["config"]
    config["theme"] = theme
    save_config(config)
    await hub.broadcast(_appearance_message())
    return JSONResponse({"ok": True, "theme": theme})


@app.get("/api/transcript")
async def api_get_transcript():
    writer: TranscriptWriter = state["transcript_writer"]
    return JSONResponse({"enabled": writer.enabled, "current_file": writer.current_filename})


@app.post("/api/transcript/start")
async def api_start_transcript():
    writer: TranscriptWriter = state["transcript_writer"]
    path = writer.start_new()
    config = state["config"]
    config["save_transcript"] = True
    save_config(config)
    return JSONResponse({"ok": True, "filename": path.name})


@app.post("/api/transcript/stop")
async def api_stop_transcript():
    writer: TranscriptWriter = state["transcript_writer"]
    writer.stop()
    config = state["config"]
    config["save_transcript"] = False
    save_config(config)
    return JSONResponse({"ok": True})


@app.get("/api/transcripts")
async def api_list_transcripts():
    transcript_dir = Path(state["config"]["transcript_dir"])
    if not transcript_dir.is_dir():
        return JSONResponse([])
    files = []
    for path in transcript_dir.rglob("*.txt"):
        rel = path.relative_to(transcript_dir)
        parts = rel.parts
        if len(parts) > 2:
            continue  # ignore anything nested deeper than group/file -- never created by this app
        stat = path.stat()
        files.append({
            "relpath": str(rel).replace("\\", "/"),
            "filename": parts[-1],
            "group": parts[0] if len(parts) == 2 else None,
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
        })
    files.sort(key=lambda f: f["modified"], reverse=True)
    return JSONResponse(files)


# Windows-invalid filename characters, plus the path separators themselves
# so a "rename" or "group" value can never be used to climb out of
# transcript_dir the way the old exact-filename check used to guard against.
_SAFE_TRANSCRIPT_NAME_RE = re.compile(r'^[^\\/:*?"<>|]+$')


def _resolve_transcript_path(relpath: str) -> Path | None:
    transcript_dir = Path(state["config"]["transcript_dir"]).resolve()
    parts = relpath.replace("\\", "/").split("/")
    if len(parts) not in (1, 2) or any(p in ("", ".", "..") for p in parts):
        return None
    target = (transcript_dir / Path(*parts)).resolve()
    try:
        target.relative_to(transcript_dir)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


@app.get("/api/transcripts/{relpath:path}/export.srt")
async def api_export_transcript_srt(relpath: str):
    target = _resolve_transcript_path(relpath)
    if target is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return PlainTextResponse(to_srt(target.read_text(encoding="utf-8")), media_type="application/x-subrip")


@app.get("/api/transcripts/{relpath:path}/export.vtt")
async def api_export_transcript_vtt(relpath: str):
    target = _resolve_transcript_path(relpath)
    if target is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return PlainTextResponse(to_vtt(target.read_text(encoding="utf-8")), media_type="text/vtt")


# Registered after the two /export.* routes above: FastAPI/Starlette tries
# routes in registration order, and this one's {relpath:path} greedily
# matches ANY continuation (slashes included) -- if it came first, a request
# for ".../export.srt" would match here instead, with "export.srt" folded
# into relpath, and 404 since no file is literally named that.
@app.get("/api/transcripts/{relpath:path}")
async def api_get_transcript_file(relpath: str):
    target = _resolve_transcript_path(relpath)
    if target is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return PlainTextResponse(target.read_text(encoding="utf-8"))


@app.post("/api/transcripts/{relpath:path}/rename")
async def api_rename_transcript(relpath: str, payload: dict):
    target = _resolve_transcript_path(relpath)
    if target is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    new_name = str(payload.get("name", "")).strip()
    if not new_name or not _SAFE_TRANSCRIPT_NAME_RE.match(new_name):
        return JSONResponse({"error": "Invalid name."}, status_code=400)
    if not new_name.lower().endswith(".txt"):
        new_name += ".txt"
    new_path = target.parent / new_name
    if new_path.exists():
        return JSONResponse({"error": "A transcript with that name already exists."}, status_code=400)
    try:
        target.rename(new_path)
    except OSError as exc:
        return JSONResponse({"error": f"Couldn't rename: {exc}"}, status_code=400)
    return JSONResponse({"ok": True, "filename": new_name})


@app.post("/api/transcripts/{relpath:path}/group")
async def api_set_transcript_group(relpath: str, payload: dict):
    target = _resolve_transcript_path(relpath)
    if target is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    group = str(payload.get("group", "")).strip()
    if group and not _SAFE_TRANSCRIPT_NAME_RE.match(group):
        return JSONResponse({"error": "Invalid group name."}, status_code=400)
    transcript_dir = Path(state["config"]["transcript_dir"]).resolve()
    dest_dir = (transcript_dir / group) if group else transcript_dir
    dest_path = dest_dir / target.name
    if dest_path == target:
        return JSONResponse({"ok": True})
    if dest_path.exists():
        return JSONResponse({"error": "A transcript with that name already exists in that group."}, status_code=400)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        target.rename(dest_path)
    except OSError as exc:
        return JSONResponse({"error": f"Couldn't move: {exc}"}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/transcripts/{relpath:path}/delete")
async def api_delete_transcript(relpath: str):
    target = _resolve_transcript_path(relpath)
    if target is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        target.unlink()
    except OSError as exc:
        return JSONResponse({"error": f"Couldn't delete: {exc}"}, status_code=400)
    return JSONResponse({"ok": True})


@app.get("/api/settings")
async def api_get_settings():
    return JSONResponse(state["config"]["appearance"])


@app.post("/api/settings")
async def api_set_settings(payload: dict):
    config = state["config"]
    for surface in ("audience", "overlay"):
        if surface in payload:
            config["appearance"][surface].update(payload[surface])
            # No design-imposed cap here -- the operator knows their own
            # screen and font size better than a fixed default does, and
            # asked for this to be fully customizable so captions can fill
            # the screen if that's the look they want. MAX_LINES_SAFETY_CAP
            # is purely a sanity bound (nothing enforces this as "the right
            # number of lines"), not a design opinion about readability.
            if "max_lines" in config["appearance"][surface]:
                config["appearance"][surface]["max_lines"] = max(
                    1, min(MAX_LINES_SAFETY_CAP, int(config["appearance"][surface]["max_lines"]))
                )
    save_config(config)
    await hub.broadcast(_appearance_message())
    return JSONResponse({"ok": True, "appearance": config["appearance"]})
