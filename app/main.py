"""AT LiveCaption control server.

Serves the control page (/), audience screen (/audience) and camera overlay
(/overlay), and fans out live captions over a single WebSocket (/ws).
Everything runs locally: no audio ever leaves this process, and nothing is
sent to any external service.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core import audio_devices, model_download, resources
from app.core.asr_engine import CaptionEngine
from app.core.config import THEMES, load_config, save_config
from app.core.hub import ConnectionHub
from app.core.transcript import TranscriptWriter
from app import version

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="AT LiveCaption")
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

        try:
            engine.load_model(str(model_dir))
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
    return JSONResponse(
        {
            "app_version": version.APP_VERSION,
            "asr_engine": version.ASR_ENGINE,
            "model_name": version.MODEL_NAME,
            "model_source_url": version.MODEL_SOURCE_URL,
            "save_transcript": state["config"]["save_transcript"],
            "transcript_dir": state["config"]["transcript_dir"],
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


@app.post("/api/test-caption")
async def api_test_caption():
    await hub.broadcast(
        {"type": "final", "text": "This is a test caption from AT LiveCaption."}
    )
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
    return JSONResponse({"enabled": state["config"]["save_transcript"]})


@app.post("/api/transcript")
async def api_set_transcript(payload: dict):
    enabled = bool(payload.get("enabled"))
    config = state["config"]
    config["save_transcript"] = enabled
    save_config(config)
    writer: TranscriptWriter = state["transcript_writer"]
    writer.set_enabled(enabled)
    return JSONResponse({"ok": True, "enabled": enabled})


@app.get("/api/settings")
async def api_get_settings():
    return JSONResponse(state["config"]["appearance"])


@app.post("/api/settings")
async def api_set_settings(payload: dict):
    config = state["config"]
    for surface in ("audience", "overlay"):
        if surface in payload:
            config["appearance"][surface].update(payload[surface])
    save_config(config)
    await hub.broadcast(_appearance_message())
    return JSONResponse({"ok": True, "appearance": config["appearance"]})
