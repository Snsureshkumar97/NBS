#!/usr/bin/env python3
"""
validate_palette.py — check a palette before trusting it
================================================================================
theme.py records validator output next to every colour that carries meaning.
This is that validator, so the claim can be re-run instead of believed.

Three questions, none of which the eye answers reliably:

  1. CONTRAST   does this colour read against the surface it sits on?
                WCAG 2.1 relative luminance, 3:1 floor for large/bold text
                and UI marks.
  2. CVD        do two colours that mean opposite things stay distinguishable
                to a colourblind reader? Dichromat simulation (Viénot 1999)
                followed by OKLab dE. 8.0 is the floor used here.
  3. ORDINAL    for a ramp, does lightness move monotonically? A ramp that
                encodes order in hue alone is not a ramp.

    python3 validate_palette.py --surface "#ffffff" --pairs "#16a34a,#dc2626"
    python3 validate_palette.py --surface "#ffffff" --ordinal "#a,#b,#c"
"""
import argparse
import math

# ---------------------------------------------------------------------------
# colour space plumbing


def hex_rgb(h):
    h = h.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rgb_hex(c):
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in c)


def _lin(u):
    return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4


def _srgb(u):
    u = max(0.0, min(1.0, u))
    return 12.92 * u if u <= 0.0031308 else 1.055 * (u ** (1 / 2.4)) - 0.055


def linear(c):
    return tuple(_lin(v) for v in c)


def gamma(c):
    return tuple(_srgb(v) for v in c)


def luminance(c):
    r, g, b = linear(c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def oklab(c):
    r, g, b = linear(c)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (v ** (1 / 3) if v > 0 else -((-v) ** (1 / 3)) for v in (l, m, s))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def de(a, b):
    """OKLab dE, scaled x100 so the numbers read like CIE dE."""
    la, aa, ba = oklab(a)
    lb, ab, bb = oklab(b)
    return 100 * math.sqrt((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2)


# Viénot, Brettel & Mollon 1999 dichromat simulation, in linear RGB.
_CVD = {
    "protan": ((0.0, 2.02344, -2.52581), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "deutan": ((1.0, 0.0, 0.0), (0.494207, 0.0, 1.24827), (0.0, 0.0, 1.0)),
    "tritan": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-0.395913, 0.801109, 0.0)),
}


def simulate(c, kind):
    r, g, b = linear(c)
    m = _CVD[kind]
    if kind == "protan":
        r2 = m[0][1] * g + m[0][2] * b
        return gamma((r2, g, b))
    if kind == "deutan":
        g2 = m[1][0] * r + m[1][2] * b
        return gamma((r, g2, b))
    b2 = m[2][0] * r + m[2][1] * g
    return gamma((r, g, b2))


# ---------------------------------------------------------------------------
# the three checks

CONTRAST_FLOOR = 3.0
CVD_FLOOR = 8.0


def check_contrast(colours, surface):
    print(f"\ncontrast on {surface}  (floor {CONTRAST_FLOOR}:1)")
    ok = True
    s = hex_rgb(surface)
    for h in colours:
        r = contrast(hex_rgb(h), s)
        good = r >= CONTRAST_FLOOR
        ok &= good
        print(f"   {h}   {r:5.2f}:1   {'PASS' if good else 'FAIL'}")
    return ok


def check_cvd(colours):
    print(f"\nCVD separation  (floor dE {CVD_FLOOR})")
    ok = True
    for i in range(len(colours)):
        for j in range(i + 1, len(colours)):
            a, b = hex_rgb(colours[i]), hex_rgb(colours[j])
            row = [f"normal {de(a, b):5.1f}"]
            worst = de(a, b)
            for kind in ("protan", "deutan", "tritan"):
                d = de(simulate(a, kind), simulate(b, kind))
                worst = min(worst, d)
                row.append(f"{kind} {d:5.1f}")
            good = worst >= CVD_FLOOR
            ok &= good
            print(f"   {colours[i]} <-> {colours[j]}   " + "  ".join(row)
                  + f"   {'PASS' if good else 'FAIL'}")
    return ok


def check_ordinal(colours, surface):
    print("\nordinal ramp")
    ls = [oklab(hex_rgb(h))[0] for h in colours]
    mono = all(ls[i] > ls[i + 1] for i in range(len(ls) - 1)) or \
        all(ls[i] < ls[i + 1] for i in range(len(ls) - 1))
    print(f"   lightness {[round(v, 3) for v in ls]}   "
          f"{'monotone PASS' if mono else 'NOT MONOTONE — FAIL'}")
    steps = [abs(ls[i] - ls[i + 1]) for i in range(len(ls) - 1)]
    step_ok = all(v >= 0.04 for v in steps)
    print(f"   adjacent dL {[round(v, 3) for v in steps]}   "
          f"{'PASS' if step_ok else 'too close — FAIL'}")
    # hue spread: a ramp should be ONE hue
    hues = []
    for h in colours:
        _, a, b = oklab(hex_rgb(h))
        hues.append(math.degrees(math.atan2(b, a)) % 360)
    spread = max(hues) - min(hues)
    hue_ok = spread <= 25
    print(f"   hue spread {spread:.0f} deg   {'PASS' if hue_ok else 'FAIL'}")
    worst = min(contrast(hex_rgb(h), hex_rgb(surface)) for h in colours)
    c_ok = worst >= CONTRAST_FLOOR
    print(f"   worst contrast on {surface}  {worst:.2f}:1   "
          f"{'PASS' if c_ok else 'FAIL'}")
    return mono and step_ok and hue_ok and c_ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--surface", default="#ffffff")
    p.add_argument("--pairs", default="")
    p.add_argument("--ordinal", default="")
    a = p.parse_args()
    ok = True
    if a.pairs:
        cols = [c for c in a.pairs.split(",") if c.strip()]
        ok &= check_contrast(cols, a.surface)
        ok &= check_cvd(cols)
    if a.ordinal:
        cols = [c for c in a.ordinal.split(",") if c.strip()]
        ok &= check_ordinal(cols, a.surface)
    print("\n" + ("ALL CHECKS PASS" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
