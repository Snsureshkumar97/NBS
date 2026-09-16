"""What is actually drawn on a Tk canvas, for the layout tests to read.

The GUI layout tests were written against a Tk stub that kept every drawn thing
in `canvas._items`, each with `.kind`, `.opts` and `.coords`, and that faked the
window size through `canvas._w` / `canvas._h`. That stub lived at /tmp/tkstub on
the machine they were written on and is not here, and on real tkinter both
idioms are traps: there is no `_items`, and `_w` is the widget's own Tcl path
name - assigning a number to it makes every later call address a widget that
does not exist ("invalid command name 1180").

This gives the same two things on top of real tkinter, so the tests exercise the
real toolkit instead of a stand-in:

    items(canvas)          -> [Item(kind, opts, coords), ...] in draw order
    fake_size(canvas, W, H) -> make the canvas report that size to skin.geom()

Item.opts reads options lazily through itemcget, so .opts.get("text") is None
for a rectangle rather than an error.
"""
import tkinter as tk


class _Opts:
    """Item options, read on demand. Unknown or unsupported ones are None."""

    def __init__(self, canvas, iid):
        self._c, self._i = canvas, iid

    def get(self, name, default=None):
        try:
            v = self._c.itemcget(self._i, name)
        except tk.TclError:
            return default
        return default if v == "" else v

    def __getitem__(self, name):
        return self.get(name)

    def __contains__(self, name):
        return self.get(name) is not None


class Item:
    __slots__ = ("id", "kind", "opts", "coords", "tags")

    def __init__(self, canvas, iid):
        self.id = iid
        self.kind = canvas.type(iid)
        self.opts = _Opts(canvas, iid)
        self.coords = list(canvas.coords(iid))
        self.tags = tuple(canvas.gettags(iid))

    def __repr__(self):
        t = self.opts.get("text")
        return f"<{self.kind} {self.coords}{'' if t is None else ' ' + repr(t)}>"


def items(canvas):
    """Everything on the canvas, in the order Tk draws it."""
    return [Item(canvas, i) for i in canvas.find_all()]


def texts(canvas):
    """Just the text items - what most of the layout assertions are about."""
    return [i for i in items(canvas) if i.kind == "text"]


_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf",          # Linux, where these were written
    "/opt/homebrew/share/fonts/DejaVuSans%s.ttf",
    "/Library/Fonts/Arial%s.ttf",
    "/System/Library/Fonts/Supplemental/Arial%s.ttf",
    "/System/Library/Fonts/Supplemental/Verdana%s.ttf",
)
_FCACHE = {}


def pil_font(px, bold=False):
    """A real font at this size, wherever the machine keeps one.

    The layout tests measure text with Pillow so a headless run agrees with the
    window. They asked for DejaVu by its Linux path, which does not exist on a
    Mac - hence the font is looked for in the usual places and, failing all of
    them, Pillow's own default is used at that size.
    """
    from PIL import ImageFont
    key = (int(px), bool(bold))
    f = _FCACHE.get(key)
    if f is None:
        for pat in _FONTS:
            path = pat % ("-Bold" if bold else "")
            try:
                f = ImageFont.truetype(path, int(px))
                break
            except OSError:
                continue
        if f is None:
            try:
                f = ImageFont.load_default(size=int(px))
            except TypeError:                       # Pillow older than 10.1
                f = ImageFont.load_default()
        _FCACHE[key] = f
    return f


def pil_measurer():
    """A measurer for skin.install_measurer(): width of `text` in pixels."""
    def measure(text, px, bold=False):
        return pil_font(px, bold).getbbox(str(text))[2]
    return measure


def fake_size(canvas, w, h):
    """Report this size to whoever asks, without a window manager.

    skin.geom() sizes the whole screen from canvas.winfo_width()/height(), and
    an unmapped window reports 1x1 until something maps and updates it. The
    layout tests are about the arithmetic at a given size, not about Tk's
    geometry propagation, so the size is simply stated. (The old stub did this
    by assigning canvas._w / canvas._h, which on real tkinter renames the
    widget and breaks every call after it.)
    """
    canvas.winfo_width = lambda: w
    canvas.winfo_height = lambda: h
    return canvas
