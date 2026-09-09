"""
Rasterise Tk canvas draw calls with PIL, so a layout can be looked at without a
display. This is not a Tk emulator — it implements exactly the item types the UI
kit uses, and it is deliberately picky: an unknown option raises, so a drawing
call that would behave differently on real Tk gets caught here instead of on the
user's machine.
"""
import math
from PIL import Image, ImageDraw, ImageFont

S = 2  # supersample; the PNG is downscaled at the end, Tk itself has no AA

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"]


class Recorder:
    """Quacks like tk.Canvas for the handful of calls the UI kit makes."""

    def __init__(self, width, height):
        self._w, self._h = width, height
        self.ops = []
        self._id = 0

    # -- canvas surface ---------------------------------------------------
    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h

    def _add(self, kind, coords, kw):
        self._id += 1
        self.ops.append((kind, list(coords), kw, self._id))
        return self._id

    # -- items ------------------------------------------------------------
    def create_line(self, *c, **k):
        return self._add("line", _flat(c), k)

    def create_rectangle(self, *c, **k):
        return self._add("rect", _flat(c), k)

    def create_polygon(self, *c, **k):
        return self._add("poly", _flat(c), k)

    def create_oval(self, *c, **k):
        return self._add("oval", _flat(c), k)

    def create_arc(self, *c, **k):
        return self._add("arc", _flat(c), k)

    def create_text(self, *c, **k):
        return self._add("text", _flat(c), k)

    # -- the bits of the canvas API the skin calls -------------------------
    def delete(self, *tags):
        if not tags or "all" in tags:
            self.ops = []
        else:
            keep = []
            for op in self.ops:
                t = op[2].get("tags", ())
                t = (t,) if isinstance(t, str) else tuple(t)
                if not any(x in t for x in tags):
                    keep.append(op)
            self.ops = keep

    def itemconfigure(self, *a, **k):
        pass

    itemconfig = itemconfigure

    def configure(self, *a, **k):
        pass

    config = configure

    def bind(self, *a, **k):
        pass

    def tag_bind(self, *a, **k):
        pass

    def find_withtag(self, tag):
        return []

    def pack(self, *a, **k):
        pass

    def place(self, *a, **k):
        pass

    def grid(self, *a, **k):
        pass


def _flat(c):
    if len(c) == 1 and isinstance(c[0], (list, tuple)):
        return list(c[0])
    return list(c)


_FONT_CACHE = {}


def _font(spec):
    if not spec:
        spec = ("DejaVu Sans", 11)
    fam = spec[0]
    size = abs(int(spec[1])) if len(spec) > 1 else 11
    weight = spec[2] if len(spec) > 2 else "normal"
    key = (spec[1] if len(spec) > 1 else 11, weight)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    suffix = "-Bold" if weight == "bold" else ""
    # Tk reads a NEGATIVE size as pixels and a positive one as points. The skin
    # uses pixels throughout so the layout is exact; honour both here or every
    # label renders a third too large.
    px = size if (len(spec) > 1 and spec[1] < 0) else size * 1.333
    f = None
    for pat in FONT_DIRS:
        try:
            f = ImageFont.truetype(pat % suffix, max(6, int(px * S)))
            break
        except Exception:
            continue
    if f is None:
        f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _col(v):
    if v in (None, "", "none"):
        return None
    return v


def render(rec, bg="#08070e", path="out.png"):
    W, H = rec.winfo_width(), rec.winfo_height()
    img = Image.new("RGB", (W * S, H * S), bg)
    d = ImageDraw.Draw(img)

    for kind, c, k, _ in rec.ops:
        cc = [v * S for v in c]
        w = max(1, int(round(k.get("width", 1) * S)))
        if kind == "line":
            fill = _col(k.get("fill", "#000000"))
            if fill is None:
                continue
            pts = list(zip(cc[0::2], cc[1::2]))
            if k.get("dash"):
                _dashed(d, pts, fill, w, k["dash"])
            else:
                joint = "curve" if k.get("capstyle") == "round" or len(pts) > 2 else None
                d.line(pts, fill=fill, width=w, joint=joint)
                if k.get("capstyle") == "round" or k.get("joinstyle") == "round":
                    r = w / 2.0
                    for x, y in pts:
                        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)
        elif kind in ("rect", "oval"):
            fn = d.rectangle if kind == "rect" else d.ellipse
            fn([cc[0], cc[1], cc[2], cc[3]], fill=_col(k.get("fill")),
               outline=_col(k.get("outline")), width=w)
        elif kind == "poly":
            pts = list(zip(cc[0::2], cc[1::2]))
            f = _col(k.get("fill"))
            o = _col(k.get("outline"))
            if f:
                d.polygon(pts, fill=f)
            if o:
                d.line(pts + [pts[0]], fill=o, width=w, joint="curve")
        elif kind == "arc":
            x0, y0, x1, y1 = cc[0], cc[1], cc[2], cc[3]
            start = k.get("start", 0.0)
            extent = k.get("extent", 90.0)
            # Tk measures anticlockwise from 3 o'clock; PIL measures clockwise.
            a0 = -(start + extent)
            a1 = -start
            if a0 > a1:
                a0, a1 = a1, a0
            if k.get("style") == "arc":
                d.arc([x0, y0, x1, y1], a0, a1, fill=_col(k.get("outline")), width=w)
            else:
                d.pieslice([x0, y0, x1, y1], a0, a1, fill=_col(k.get("fill")),
                           outline=_col(k.get("outline")), width=w)
        elif kind == "text":
            txt = str(k.get("text", ""))
            if not txt:
                continue
            f = _font(k.get("font"))
            # "center" contains both an "n" and an "e", so substring tests on it
            # silently right-align and top-align every centred label. Normalise
            # first — this cost an hour of believing the layout was wrong.
            anchor = k.get("anchor", "center")
            anchor = "" if anchor in ("center", "centre", "c") else anchor
            # Tk wraps at `width` pixels. Ignoring it here made the preview
            # kinder than the real window: a sub-line that Tk folds onto a
            # second row was drawn as one long line running out of its card.
            wrap = k.get("width")
            if wrap:
                txt = _wrap(txt, f, float(wrap) * S)
            x, y = cc[0], cc[1]
            bbox = d.multiline_textbbox((0, 0), txt, font=f,
                                        align=k.get("justify", "left"))
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if "w" in anchor:
                ax = x
            elif "e" in anchor:
                ax = x - tw
            else:
                ax = x - tw / 2.0
            if "n" in anchor:
                ay = y
            elif "s" in anchor:
                ay = y - th
            else:
                ay = y - th / 2.0
            d.multiline_text((ax - bbox[0], ay - bbox[1]), txt, font=f,
                             fill=_col(k.get("fill", "#ffffff")),
                             align=k.get("justify", "left"))
    img = img.resize((W, H), Image.LANCZOS)
    img.save(path)
    return path


def _wrap(text, font, px):
    """Greedy word wrap at a pixel width, the way Tk's -width option does."""
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = word if not line else line + " " + word
            if font.getbbox(trial)[2] <= px or not line:
                line = trial
            else:
                out.append(line)
                line = word
        out.append(line)
    return "\n".join(out)


def _dashed(d, pts, fill, w, dash):
    on = dash[0] * S
    off = (dash[1] if len(dash) > 1 else dash[0]) * S
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        t = 0.0
        while t < L:
            e = min(L, t + on)
            d.line([(x0 + (x1 - x0) * t / L, y0 + (y1 - y0) * t / L),
                    (x0 + (x1 - x0) * e / L, y0 + (y1 - y0) * e / L)],
                   fill=fill, width=w)
            t = e + off
