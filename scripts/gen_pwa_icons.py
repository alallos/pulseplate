#!/usr/bin/env python3
"""Generate simple PulsePlate PWA icons (stdlib only; no Pillow). Run from repo root."""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def write_png_rgb(path: Path, width: int, height: int, rgb_at) -> None:
    """rgb_at(y, x) -> (r, g, b) in 0..255."""

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: None
        for x in range(width):
            r, g, b = rgb_at(y, x)
            raw.extend((r, g, b))

    compressed = zlib.compress(bytes(raw), 9)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def pulseplate_pixel(size: int):
    """Dark surface + accent circle + subtle green arc."""

    cx = cy = (size - 1) / 2.0
    r_inner = min(size, size) * 0.38
    r_ring = min(size, size) * 0.48

    def rgb_at(y: int, x: int) -> tuple[int, int, int]:
        dx, dy = x - cx, y - cy
        d = math.hypot(dx, dy)
        # background
        bg = (26, 35, 50)
        if d <= r_inner:
            return (88, 166, 255)
        if r_inner < d <= r_ring:
            return (45, 60, 85)
        # subtle pulse hint along bottom arc
        if y > cy and d < r_ring + size * 0.08:
            t = (x / size) * math.pi
            wave = math.sin(t * 3) * 0.5 + 0.5
            g = int(63 + wave * 40)
            return (30, g, 55)
        return bg

    return rgb_at


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    out = root / "app" / "static" / "icons"
    for size in (192, 512):
        write_png_rgb(out / f"icon-{size}.png", size, size, pulseplate_pixel(size))
    print(f"Wrote {out / 'icon-192.png'} and {out / 'icon-512.png'}")


if __name__ == "__main__":
    main()
