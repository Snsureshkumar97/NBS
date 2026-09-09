"""Every rail page must render without throwing — and without SHOWING a crash.

This is the test that was missing: the settings page had no coverage, so a
NameError in it reached the user as 'UNKNOWN - name app is not defined'
printed calmly on screen where the token status belongs.
"""
import sys, os, csv, tempfile, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, gui, skin, theme as T, trade_log
from PIL import ImageFont
_fc = {}
def _pil(t, px, b=False):
    f = _fc.get((px, bool(b)))
    if f is None:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
                               % ("-Bold" if b else ""), int(px))
        _fc[(px, bool(b))] = f
    return f.getbbox(t)[2]
skin.install_measurer(_pil)

path = os.path.join(tempfile.mkdtemp(), "trades.csv")
with open(path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=trade_log.FIELDS); w.writeheader()
    w.writerow({"event": "CLOSE", "date": "2026-08-31", "index": "NIFTY",
                "pnl": 2100, "lots": 1, "status": "CLOSED — T3 hit"})
trade_log._log_path = lambda: path

# Anything that looks like a Python error leaking onto the screen.
SMELLS = ("not defined", "Traceback", "NoneType", "KeyError", "AttributeError",
          "TypeError", "IndexError", "object has no attribute", "Error:")

root = tk.Tk(); app = gui.SignalApp(root); root.bell = lambda: None
sk = app.bridge.skin
sk.canvas._w, sk.canvas._h = 1570, 1002

bad = []
for mode in ("dark", "light"):
    T.use(mode)
    for page in ("board", "map", "review", "settings"):
        app.bridge._rail(page)                  # exactly what a rail click does
        app.bridge._painted = False
        app.bridge.flush()
        texts = [str(i.opts.get("text", "")) for i in sk.canvas._items
                 if i.kind == "text"]
        for t in texts:
            for smell in SMELLS:
                if smell in t:
                    bad.append((mode, page, t[:90]))
        n = len([t for t in texts if t.strip()])
        assert n > 3, f"{mode}/{page}: drew almost nothing ({n} labels)"
        print(f"  {mode:5s} {page:9s} {n:3d} labels, no error text")
T.use("dark")

if bad:
    for b in bad:
        print("  LEAKED:", b)
    raise AssertionError(f"{len(bad)} page(s) showed a Python error to the user")

# and the settings page must actually report the token state it was asked for
app._token_state, app._token_detail = "ok", "valid until the morning flush"
app.bridge._rail("settings")
app.bridge._painted = False
app.bridge.flush()
texts = [str(i.opts.get("text", "")) for i in sk.canvas._items if i.kind == "text"]
assert any("OK — valid until" in t for t in texts), \
    f"settings did not show the real token state: {[t for t in texts if 'token' in t.lower() or '—' in t][:4]}"
print("\n  settings reports the cached token state correctly")

print("\nPAGES TEST PASSED")
