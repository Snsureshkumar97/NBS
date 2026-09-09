import sys, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude")
sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, tkraster, skin, gui, theme as T, config
from PIL import ImageFont
_fc={}
def _pil(t,px,b=False):
    f=_fc.get((px,bool(b)))
    if f is None:
        f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"%("-Bold" if b else ""),int(px)); _fc[(px,bool(b))]=f
    return f.getbbox(t)[2]
skin.install_measurer(_pil)

gui.now_ist = lambda: dt.datetime(2026, 8, 31, 11, 42, 5)
root = tk.Tk(); app = gui.SignalApp(root); root.bell = lambda: None
gui._load_demo(app)
# a populated vote list so the new gauge bars have something to show
for nm, arrow, val, col in (("Trend","↑","", gui.GREEN), ("MACD","↑","+30.05", gui.GREEN),
                            ("RSI","↑","60", gui.GREEN), ("VWAP","–","+0", gui.FG_MUTED),
                            ("PCR","–","1.02", gui.FG_MUTED)):
    app.ind_labels[nm].config(text=arrow, fg=col)
    app.ind_values[nm].config(text=val)
app.adx_label.config(text="27.1 PASS", fg=gui.GREEN)
app.reach_label.config(text="↑399 ↓399", fg=gui.FG_SECOND)
app.confidence_pct = 75
app.conf_label.config(text="MEDIUM", fg=gui.AMBER)

for mode in ("light", "dark"):
    T.use(mode)
    sk = app.bridge.skin
    sk.state["theme"] = mode
    sk.canvas = tkraster.Recorder(1570, 1002)
    app.bridge._painted = False
    app.bridge.flush()
    tkraster.render(sk.canvas, bg=T.BG_APP, path=f"/tmp/theme_{mode}.png")
    print("wrote", mode, T.BG_APP)
