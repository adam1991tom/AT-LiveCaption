"""Server-only entry point: `python -m app.server_main` or the frozen exe's --server mode."""
import uvicorn

from app.core.procutil import get_port
from app.main import app


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=get_port(), log_level="info")


if __name__ == "__main__":
    main()
