"""WebSocket fan-out: one caption/status stream broadcast to every connected page."""
from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

from fastapi import WebSocket


class ConnectionHub:
    def __init__(self, history_size: int = 5) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.recent_finals: deque[str] = deque(maxlen=history_size)
        self.current_partial: str = ""
        self.appearance: dict[str, Any] = {}
        self.status: dict[str, Any] = {
            "audio": {"connected": False, "device": None, "level_db": -100.0},
            "engine": {"ready": False, "state": "starting"},
        }

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        # Bring the newly-connected page (e.g. a refreshed audience TV) up to date.
        await self._send(ws, {"type": "appearance", **self.appearance})
        await self._send(
            ws,
            {
                "type": "sync",
                "recent_finals": list(self.recent_finals),
                "partial": self.current_partial,
            },
        )
        await self._send(ws, {"type": "status", **self.status})

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def _send(self, ws: WebSocket, message: dict[str, Any]) -> None:
        try:
            await ws.send_text(json.dumps(message))
        except Exception:
            async with self._lock:
                self._clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        if message.get("type") == "final":
            self.recent_finals.append(message["text"])
            self.current_partial = ""
        elif message.get("type") == "partial":
            self.current_partial = message["text"]
        elif message.get("type") == "clear":
            self.recent_finals.clear()
            self.current_partial = ""
        elif message.get("type") == "status":
            self.status = {k: v for k, v in message.items() if k != "type"}
        elif message.get("type") == "appearance":
            self.appearance = {k: v for k, v in message.items() if k != "type"}

        async with self._lock:
            targets = list(self._clients)
        for ws in targets:
            await self._send(ws, message)

    def client_count(self) -> int:
        return len(self._clients)
