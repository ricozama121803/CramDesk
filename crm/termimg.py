"""Show PDF pages inline in terminals that speak the kitty graphics protocol (kitty, ghostty, wezterm)."""
from __future__ import annotations

import base64
import fcntl
import io
import os
import shutil
import struct
import subprocess
import sys
import termios
from pathlib import Path


def supports_inline_images() -> bool:
    if not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "").lower()
    prog = os.environ.get("TERM_PROGRAM", "").lower()
    return (
        "kitty" in term or "ghostty" in term or prog in ("ghostty", "wezterm")
        or "KITTY_WINDOW_ID" in os.environ
    )


def _term_geometry() -> tuple[int, int, float, float]:
    """cols, rows, cell width px, cell height px"""
    try:
        rows, cols, xpx, ypx = struct.unpack("HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
        if cols and rows and xpx and ypx:
            return cols, rows, xpx / cols, ypx / rows
    except OSError:
        pass
    size = shutil.get_terminal_size()
    return size.columns, size.lines, 8.0, 17.0


def _pages(pdf: Path, pages: list[int] | None):
    import pypdfium2 as pdfium
    from PIL import Image

    doc = pdfium.PdfDocument(str(pdf))
    idx = [p - 1 for p in pages] if pages else range(len(doc))
    imgs = [doc[i].render(scale=2.5).to_pil().convert("RGB") for i in idx if 0 <= i < len(doc)]
    gap = 40
    w = sum(i.width for i in imgs) + gap * (len(imgs) - 1)
    h = max(i.height for i in imgs)
    sheet = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x = 0
    for im in imgs:
        sheet.paste(im, (x, 0))
        x += im.width + gap
    return sheet


def show_inline(pdf: Path, pages: list[int] | None = None, reserve_rows: int = 4) -> None:
    from PIL import Image

    img = _pages(pdf, pages)
    cols, rows, cw, ch = _term_geometry()
    max_w, max_h = (cols - 2) * cw, (rows - reserve_rows) * ch
    scale = min(max_w / img.width, max_h / img.height)
    px_w, px_h = max(1, int(img.width * scale)), max(1, int(img.height * scale))
    img = img.resize((px_w, px_h), Image.LANCZOS)
    c, r = max(1, round(px_w / cw)), max(1, round(px_h / ch))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = base64.standard_b64encode(buf.getvalue())
    out = sys.stdout.buffer
    first = True
    while data:
        chunk, data = data[:4096], data[4096:]
        more = 1 if data else 0
        head = f"a=T,f=100,q=2,c={c},r={r},m={more}" if first else f"m={more}"
        out.write(b"\x1b_G" + head.encode() + b";" + chunk + b"\x1b\\")
        first = False
    out.write(b"\n")
    out.flush()


def open_external(path: Path) -> bool:
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if not shutil.which(opener):
        return False
    subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True
