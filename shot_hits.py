"""A live ticket with T1 and T2 taken out — what a hit actually looks like."""
import sys, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude")
sys.path.insert(0, "/home/claude/trading-tool")
import numpy as np, pandas as pd
import tkinter as tk
import tkraster, skin as _skin

from PIL import ImageFont
_fc = {}
def _pil(text, px, bold=False):
    f = _fc.get((px, bool(bold)))
    if f is None:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
                               % ("-Bold" if bold else ""), int(px))
        _fc[(px, bool(bold))] = f
    return f.getbbox(text)[2]
_skin.install_measurer(_pil)

import config, signal_engine as SE, main as M, gui

NOW = dt.datetime(2026, 8, 19, 11, 42, 5)
for mod in (gui, M, SE):
    if hasattr(mod, "now_ist"):
        mod.now_ist = lambda: NOW

# SENSEX falling — the PE is in profit
rs = np.random.RandomState(7)
n = 250
idx = []
d = dt.datetime(2026, 8, 6, 9, 15)
while len(idx) < n:
    if d.weekday() < 5:
        t = d.replace(hour=9, minute=15)
        for _ in range(25):
            idx.append(t); t += dt.timedelta(minutes=15)
    d += dt.timedelta(days=1)
idx = pd.DatetimeIndex(idx[:n])
close = 78600 + np.cumsum(rs.randn(n) * 46 - 7.2)
close[-20:] = close[-20] - np.cumsum(np.abs(rs.randn(20)) * 48)
op = np.concatenate([[close[0]], close[:-1]])
df = pd.DataFrame({"Open": op,
                   "High": np.maximum(op, close) + np.abs(rs.randn(n)) * 30,
                   "Low": np.minimum(op, close) - np.abs(rs.randn(n)) * 30,
                   "Close": close, "Volume": rs.randint(9000, 40000, n)}, index=idx)

tech = SE.compute_technical_signal(df)
oi = SE.compute_option_chain_signal(None)
rec = SE.build_recommendation("SENSEX", tech, oi,
                              config.INSTRUMENTS["SENSEX"]["strike_step"], reach=None)
rec["candles"] = df
rec["trend"] = SE.compute_market_trend(df)
report = M.format_report(rec)

root = tk.Tk()
app = gui.SignalApp(root)
app.msg_queue.put(("report", "SENSEX", rec, report, []))
class R:
    def after(self, *a): pass
    def bell(self): pass
app.root = R()
app.index_var.set("SENSEX"); app.current = "SENSEX"; app._focus = "SENSEX"
app.popup_var.set(False)
gui.SignalApp._poll_queue(app)

# the ticket from your screenshot: entry 196.95, premium-tracked, 75 lot
trade = {
    "index": "SENSEX", "option_type": "PE", "strike": 77100,
    "entry_time": "09:18:31", "entry_ts": NOW,
    "why": None, "entry_spot": 77218.40, "entry_ltp": 196.95,
    "use_premium": True, "lot_size": 20,
    "index_targets": [77020.0, 76940.0, 76860.0], "index_sl": 77330.0,
    "premium_targets": [261.31, 309.58, 357.84], "premium_sl": 148.20,
    "hit": {"T1": True, "T2": False, "T3": False},
    "hit_time": {"T1": "10:07:44", "T2": None, "T3": None},
    "sl_hit": False, "sl_hit_time": None, "status": "OPEN",
    "trade_id": "SENSEX-20260819-091831",
}
app.active_trade = trade
app.last_rec = rec

# price is now 294.50 — that takes T2 out while we watch
app._check_trade_price(294.50)
print("T1:", trade["hit"]["T1"], trade["hit_time"]["T1"])
print("T2:", trade["hit"]["T2"], trade["hit_time"]["T2"])
print("T3:", trade["hit"]["T3"], "  status:", trade["status"])
print("progress:", {k: round(v, 2) for k, v in app.level_progress.items()})

W, H = 1570, 1002
sk = app.bridge.skin
sk.canvas = tkraster.Recorder(W, H)
app.bridge._painted = False
app.bridge.flush()
tkraster.render(sk.canvas, path="/tmp/hit_targets.png")
print("wrote /tmp/hit_targets.png")
