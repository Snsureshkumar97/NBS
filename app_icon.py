#!/usr/bin/env python3
"""
app_icon.py — draw the app icon, with nothing installed
================================================================================
Writes a PNG using only zlib and struct from the standard library. Pillow is not
a dependency of this tool and adding one just to make an icon would be a poor
trade, so the few hundred lines of image library we actually need live here.

    python3 app_icon.py out.png [size]

The icon is a dark rounded square with three candles stepping up and a stop line
underneath — the same colours the chart panel uses, so the icon and the app look
like the same piece of software.
"""

import struct
import zlib

# Same palette as chart_panel.py. GREEN/RED here are the validated pair that
# stay distinguishable under deuteranopia; do not "improve" them by eye.
BG_DARK = (0x0b, 0x0d, 0x12)
BG_TILE = (0x14, 0x19, 0x24)
GREEN = (0x19, 0x9e, 0x70)
RED = (0xef, 0x44, 0x44)
BLUE = (0x39, 0x87, 0xe5)
MUTED = (0x6b, 0x72, 0x80)


def _blend(dst, src, a):
    """Composite `src` over `dst` with coverage a in 0..1."""
    return tuple(int(round(d + (s - d) * a)) for d, s in zip(dst, src))


class Canvas:
    def __init__(self, size, bg):
        self.n = size
        self.px = [[bg for _ in range(size)] for _ in range(size)]

    def rect(self, x0, y0, x1, y1, colour, alpha=1.0):
        n = self.n
        for y in range(max(0, int(y0)), min(n, int(round(y1)))):
            row = self.px[y]
            for x in range(max(0, int(x0)), min(n, int(round(x1)))):
                row[x] = _blend(row[x], colour, alpha) if alpha < 1 else colour

    def rounded(self, x0, y0, x1, y1, r, colour):
        """Antialiased rounded rectangle — the only shape here that needs edge
        smoothing, because a hard-cornered icon looks broken next to every other
        icon in the Dock."""
        n = self.n
        for y in range(max(0, int(y0) - 1), min(n, int(y1) + 2)):
            for x in range(max(0, int(x0) - 1), min(n, int(x1) + 2)):
                cx = min(max(x + 0.5, x0 + r), x1 - r)
                cy = min(max(y + 0.5, y0 + r), y1 - r)
                d = ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5
                a = min(1.0, max(0.0, r + 0.5 - d))
                if a > 0:
                    self.px[y][x] = _blend(self.px[y][x], colour, a)

    def to_png(self, path):
        raw = bytearray()
        for row in self.px:
            raw.append(0)                      # filter type 0 for every scanline
            for r, g, b in row:
                raw += bytes((r, g, b, 255))

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

        ihdr = struct.pack(">IIBBBBB", self.n, self.n, 8, 6, 0, 0, 0)
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
               + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
               + chunk(b"IEND", b""))
        with open(path, "wb") as f:
            f.write(png)
        return path


def draw(size=512):
    c = Canvas(size, BG_DARK)
    u = size / 100.0                            # one "unit" = 1% of the icon

    # macOS rounds icons itself on newer systems but not everywhere, and Windows
    # never does, so draw the tile.
    c.rounded(4 * u, 4 * u, 96 * u, 96 * u, 21 * u, BG_TILE)

    # Three candles stepping up: red, green, green. Body plus wick.
    # Corners barely rounded — real candles are rectangles, and a large radius
    # at this scale reads as a pill rather than a candle.
    # (x_centre, body_top, body_bottom, wick_top, wick_bottom, colour)
    candles = [
        (30, 46, 63, 39, 69, RED),
        (50, 33, 52, 26, 58, GREEN),
        (70, 19, 39, 14, 45, GREEN),
    ]
    for cx, top, bot, wtop, wbot, col in candles:
        c.rect((cx - 1.7) * u, wtop * u, (cx + 1.7) * u, wbot * u, col)
        c.rounded((cx - 7.5) * u, top * u, (cx + 7.5) * u, bot * u, 1.2 * u, col)

    # The stop line — a dashed rule under the move. Sizing the gap from the span
    # rather than hardcoding it means the last dash lands flush with the right
    # edge instead of being sliced off.
    x0, x1, y = 17 * u, 83 * u, 79 * u
    dashes = 7
    pitch = (x1 - x0) / (dashes - 0.35)
    for i in range(dashes):
        left = x0 + i * pitch
        c.rounded(left, y, left + pitch * 0.65, y + 2.0 * u, 1.0 * u, BLUE)
    return c


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 512
    print(draw(n).to_png(out))
