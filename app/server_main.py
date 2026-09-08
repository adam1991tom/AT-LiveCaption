"""Server-only entry point: `python -m app.server_main` or the frozen exe's --server mode."""
import os

import uvicorn

from app.main import app

DEFAULT_PORT = 8765


def get_port() -> int:
    return int(os.environ.get("AT_LIVECAPTION_PORT", DEFAULT_PORT))


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=get_port(), log_level="info")


if __name__ == "__main__":
    main()
