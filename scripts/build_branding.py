"""Generates per-theme app icons (.ico), installer wizard banners (.bmp), and
web-friendly PNGs from the source AT gear logo artwork in branding/source/.

Run after replacing/adding a source logo:
    .venv\\Scripts\\python.exe scripts\\build_branding.py
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "branding"
WEB_OUT = ROOT / "app" / "static" / "branding"
ICO_OUT = ROOT / "branding" / "ico"
WIZ_OUT = ROOT / "installer" / "wizard"

THEMES = ["blue", "green", "purple", "orange"]

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
WIZARD_LARGE = (164, 314)  # Inno Setup WizardImageFile
WIZARD_SMALL = (55, 58)  # Inno Setup WizardSmallImageFile


def load_square(theme: str) -> Image.Image:
    img = Image.open(SRC_DIR / f"at_gear_{theme}.png").convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    return img


def make_ico(theme: str, img: Image.Image) -> None:
    ICO_OUT.mkdir(parents=True, exist_ok=True)
    path = ICO_OUT / f"at_livecaption_{theme}.ico"
    sized = [img.resize((s, s), Image.LANCZOS) for s in ICO_SIZES]
    sized[-1].save(path, format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=sized[:-1])


def make_wizard_images(theme: str, img: Image.Image) -> None:
    WIZ_OUT.mkdir(parents=True, exist_ok=True)

    # Small (55x58): logo centered on white, no text -- top-right corner mark.
    small = Image.new("RGB", WIZARD_SMALL, "white")
    logo_small = img.copy()
    logo_small.thumbnail((WIZARD_SMALL[0] - 6, WIZARD_SMALL[1] - 6), Image.LANCZOS)
    small.paste(
        logo_small,
        ((WIZARD_SMALL[0] - logo_small.width) // 2, (WIZARD_SMALL[1] - logo_small.height) // 2),
        logo_small,
    )
    small.save(WIZ_OUT / f"wizard_small_{theme}.bmp", format="BMP")

    # Large (164x314): logo near the top of the tall left-hand banner.
    large = Image.new("RGB", WIZARD_LARGE, "white")
    logo_large = img.copy()
    logo_large.thumbnail((WIZARD_LARGE[0] - 24, WIZARD_LARGE[0] - 24), Image.LANCZOS)
    large.paste(
        logo_large,
        ((WIZARD_LARGE[0] - logo_large.width) // 2, 40),
        logo_large,
    )
    large.save(WIZ_OUT / f"wizard_large_{theme}.bmp", format="BMP")


def make_web_png(theme: str, img: Image.Image) -> None:
    WEB_OUT.mkdir(parents=True, exist_ok=True)
    web = img.copy()
    web.thumbnail((512, 512), Image.LANCZOS)
    web.save(WEB_OUT / f"at_gear_{theme}.png", format="PNG")


def main() -> None:
    for theme in THEMES:
        img = load_square(theme)
        make_ico(theme, img)
        make_wizard_images(theme, img)
        make_web_png(theme, img)
        print(f"built assets for theme '{theme}'")


if __name__ == "__main__":
    main()
