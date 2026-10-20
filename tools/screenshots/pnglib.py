"""Tiny image toolkit for the README screenshots (stdlib only): read an xwd dump,
crop, stack side by side or top to bottom, stamp a small label, write a PNG.

An image is (width, height, rows) where rows is a list of bytes, 3 bytes (RGB) per pixel."""
from __future__ import annotations

import struct
import zlib

Image = tuple[int, int, list]


def read_xwd(path: str) -> Image:
    """A 24/32-bit ZPixmap xwd dump."""
    d = open(path, "rb").read()
    f = struct.unpack(">25I", d[:100])
    hsize, w, h = f[0], f[4], f[5]
    byte_order, bpp, bpl, ncolors = f[7], f[11], f[12], f[19]
    off = hsize + ncolors * 12
    n = bpp // 8
    rows = []
    for y in range(h):
        line = d[off + y * bpl: off + y * bpl + w * n]
        if byte_order == 0:  # LSBFirst: B G R [X]
            r, g, b = line[2::n], line[1::n], line[0::n]
        else:                # MSBFirst: [X] R G B
            r, g, b = line[n - 3::n], line[n - 2::n], line[n - 1::n]
        px = bytearray(w * 3)
        px[0::3], px[1::3], px[2::3] = r, g, b
        rows.append(bytes(px))
    return w, h, rows


def crop(img: Image, x: int, y: int, w: int, h: int) -> Image:
    iw, ih, rows = img
    x, y = max(0, x), max(0, y)
    w, h = min(w, iw - x), min(h, ih - y)
    return w, h, [r[x * 3:(x + w) * 3] for r in rows[y:y + h]]


def pad(img: Image, w: int, h: int, colour=(0, 0, 0)) -> Image:
    """Grow to w x h (top-left anchored) with `colour`."""
    iw, ih, rows = img
    fill = bytes(colour) * (w - iw)
    out = [r + fill for r in rows] + [bytes(colour) * w] * (h - ih)
    return w, h, out


def hstack(a: Image, b: Image, gap: int = 12, colour=(0, 0, 0)) -> Image:
    h = max(a[1], b[1])
    a, b = pad(a, a[0], h, colour), pad(b, b[0], h, colour)
    g = bytes(colour) * gap
    return a[0] + gap + b[0], h, [x + g + y for x, y in zip(a[2], b[2])]


def vstack(a: Image, b: Image, gap: int = 12, colour=(0, 0, 0)) -> Image:
    w = max(a[0], b[0])
    a, b = pad(a, w, a[1], colour), pad(b, w, b[1], colour)
    return w, a[1] + gap + b[1], a[2] + [bytes(colour) * w] * gap + b[2]


def frame(img: Image, colour, width: int = 1) -> Image:
    """Draw a border of `colour` just inside the image's edge."""
    w, h, rows = img
    c = bytes(colour)
    rows = [bytearray(r) for r in rows]
    for y in range(h):
        for x in range(w):
            if x < width or y < width or x >= w - width or y >= h - width:
                rows[y][x * 3:x * 3 + 3] = c
    return w, h, [bytes(r) for r in rows]


# 5x7 glyphs for the few letters a label needs.
_FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    " ": ("00000",) * 7,
}


def label(img: Image, text: str, x: int = 10, y: int = 10, scale: int = 2, fg=(235, 219, 178),
          bg=(60, 56, 54)) -> Image:
    """Stamp `text` (letters A B E F O R T and space only) on a filled chip at (x, y)."""
    w, h, rows = img
    rows = [bytearray(r) for r in rows]
    cw = 6 * scale
    chip_w, chip_h = len(text) * cw + 2 * scale * 2, 7 * scale + 2 * scale * 2
    for yy in range(y, min(h, y + chip_h)):
        for xx in range(x, min(w, x + chip_w)):
            rows[yy][xx * 3:xx * 3 + 3] = bytes(bg)
    for i, ch in enumerate(text.upper()):
        for gy, line in enumerate(_FONT[ch]):
            for gx, bit in enumerate(line):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            px, py = x + 2 * scale + i * cw + gx * scale + dx, y + 2 * scale + gy * scale + dy
                            if px < w and py < h:
                                rows[py][px * 3:px * 3 + 3] = bytes(fg)
    return w, h, [bytes(r) for r in rows]


def write_png(img: Image, path: str) -> None:
    w, h, rows = img

    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))

    raw = b"".join(b"\0" + r for r in rows)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
