"""Pre-fetch the fixed speech model once (e.g. as an installer step). Safe to re-run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import load_config
from app.core.model_download import download_and_extract


def main() -> None:
    model_dir = Path(load_config()["model_dir"])
    print(f"Model directory: {model_dir}")

    def progress(stage, pct):
        if pct is not None:
            print(f"\r{stage}: {pct:5.1f}%", end="", flush=True)
        else:
            print(f"\n{stage}...")

    download_and_extract(model_dir, progress_cb=progress)
    print("\nModel ready.")


if __name__ == "__main__":
    main()
