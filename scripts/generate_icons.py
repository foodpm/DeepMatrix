import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _resize_png(im: Image.Image, size: int) -> Image.Image:
    src = im.convert("RGBA")
    return src.resize((size, size), Image.LANCZOS)


def _write_ico(src_png: str, out_ico: str) -> None:
    with Image.open(src_png) as im:
        _ensure_dir(os.path.dirname(out_ico))
        sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        im.convert("RGBA").save(out_ico, format="ICO", sizes=sizes)


def _write_icns_with_iconutil(src_png: str, out_icns: str) -> bool:
    if sys.platform != "darwin":
        return False
    iconutil = shutil.which("iconutil")
    if not iconutil:
        return False
    with Image.open(src_png) as im:
        base = im.convert("RGBA")
        with tempfile.TemporaryDirectory() as td:
            iconset = os.path.join(td, "logo.iconset")
            _ensure_dir(iconset)
            mapping = [
                ("icon_16x16.png", 16),
                ("icon_16x16@2x.png", 32),
                ("icon_32x32.png", 32),
                ("icon_32x32@2x.png", 64),
                ("icon_128x128.png", 128),
                ("icon_128x128@2x.png", 256),
                ("icon_256x256.png", 256),
                ("icon_256x256@2x.png", 512),
                ("icon_512x512.png", 512),
                ("icon_512x512@2x.png", 1024),
            ]
            for name, size in mapping:
                p = os.path.join(iconset, name)
                _resize_png(base, size).save(p, format="PNG", optimize=True)
            _ensure_dir(os.path.dirname(out_icns))
            subprocess.run([iconutil, "-c", "icns", iconset, "-o", out_icns], check=True)
            return os.path.isfile(out_icns) and os.path.getsize(out_icns) > 0


def _write_icns_with_pillow(src_png: str, out_icns: str) -> bool:
    try:
        with Image.open(src_png) as im:
            _ensure_dir(os.path.dirname(out_icns))
            im.convert("RGBA").save(out_icns, format="ICNS")
        return os.path.isfile(out_icns) and os.path.getsize(out_icns) > 0
    except Exception:
        return False


def _write_icns(src_png: str, out_icns: str) -> None:
    if sys.platform != "darwin":
        return
    if _write_icns_with_iconutil(src_png, out_icns):
        return
    if _write_icns_with_pillow(src_png, out_icns):
        return
    raise RuntimeError("failed to generate .icns")


def main() -> int:
    root = _repo_root()
    src_logo_png = os.path.join(root, "logo.png")
    if not os.path.isfile(src_logo_png):
        raise FileNotFoundError(f"logo.png not found: {src_logo_png}")

    src_mac_png = os.path.join(root, "mac.png")
    if sys.platform == "darwin" and not os.path.isfile(src_mac_png):
        raise FileNotFoundError(f"mac.png not found: {src_mac_png}")

    assets_dir = os.path.join(root, "packaging", "assets")
    _ensure_dir(assets_dir)

    out_ico = os.path.join(assets_dir, "logo.ico")
    out_icns = os.path.join(assets_dir, "logo.icns")

    _write_ico(src_logo_png, out_ico)
    _write_icns(src_mac_png if sys.platform == "darwin" else src_logo_png, out_icns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
