"""Render what the window actually looks like with the market shut."""
import sys, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude")
sys.path.insert(0, "/home/claude/trading-tool")
import numpy as np, pandas as pd
import tkinter as tk
import tkraster, skin as _skin

# Measure text the way the rasteriser will actually draw it, so the picture
# agrees with the layout instead of flattering it.
from PIL import ImageFont
_fc = {}
def _pil_measure(text, px, bold=False):
    f = _fc.get((px, bool(bold)))
    if f is None:
        path = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else "")
        f = ImageFont.truetype(path, int(px))
        _fc[(px, bool(bold))] = f
    return f.getbbox(text)[2]
_skin.install_measurer(_pil_measure)

# --- a believable NIFTY session: gap up, drift, afternoon fade ----------
rs = np.random.RandomState(11)
days, per_day = 12, 25
n = days * per_day
idx = []
d = dt.datetime(2026, 8, 3, 9, 15)
while len(idx) < n:
    if d.weekday() < 5:
        t = d.replace(hour=9, minute=15)
        for _ in range(per_day):
            idx.append(t); t += dt.timedelta(minutes=15)
    d += dt.timedelta(days=1)
idx = pd.DatetimeIndex(idx[:n])

drift = np.concatenate([rs.randn(n - per_day) * 14 + 1.2,
                        rs.randn(per_day) * 16 - 4.5])       # last day fades
close = 24180 + np.cumsum(drift)
high = close + np.abs(rs.randn(n)) * 11 + 4
low = close - np.abs(rs.randn(n)) * 11 - 4
op = np.concatenate([[close[0]], close[:-1]])
df = pd.DataFrame({"Open": op, "High": np.maximum(high, np.maximum(op, close)),
                   "Low": np.minimum(low, np.minimum(op, close)),
                   "Close": close, "Volume": rs.randint(80000, 240000, n)}, index=idx)

# --- run the real engine on it -----------------------------------------
import config, signal_engine as SE, main as M, gui

CLOSED = dt.datetime(2026, 8, 18, 19, 42)
for mod in (gui, M, SE):
    if hasattr(mod, "now_ist"):
        mod.now_ist = lambda: CLOSED

tech = SE.compute_technical_signal(df)
oi = SE.compute_option_chain_signal(None)          # no chain after hours
try:
    reach = SE.compute_reachability(tech["last_close"], oi, df, CLOSED, adx=tech.get("adx"))
except Exception:
    reach = None
rec = SE.build_recommendation("NIFTY", tech, oi, config.INSTRUMENTS["NIFTY"]["strike_step"],
                              reach=reach)
rec["candles"] = df
rec["trend"] = SE.compute_market_trend(df)
report = M.format_report(rec)
print("engine says:", rec["action"], "| trend:", rec["trend"].get("label"))

# --- build the real app, feed it the snapshot the way the worker does ---
root = tk.Tk()
app = gui.SignalApp(root)
app.msg_queue.put(("closed", M.format_market_closed_banner(CLOSED)))
app.msg_queue.put(("report", "NIFTY", rec, report, [], True))
class R:
    def after(self, *a): pass
app.root = R()
gui.SignalApp._poll_queue(app)

print("header :", app.market_label.text)
print("status :", app.status_label.text)
print("badge  :", app.badge_label.text.strip())

# --- repaint onto the rasteriser and save -------------------------------
W, H = 1570, 1002
sk = app.bridge.skin
sk.canvas = tkraster.Recorder(W, H)
app.bridge._painted = False
app.bridge.flush()
tkraster.render(sk.canvas, path="/tmp/closed_signal.png")
print("wrote /tmp/closed_signal.png")

# --- and the Chart tab, same snapshot -----------------------------------
app._rail = "board"
app.view = "chart"
app._render_chart()
sk.canvas = tkraster.Recorder(W, H)
app.bridge._painted = False
app.bridge.flush()
tkraster.render(sk.canvas, path="/tmp/closed_chart.png")
print("wrote /tmp/closed_chart.png")
