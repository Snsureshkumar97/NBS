import sys, os, csv, tempfile, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude")
sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, tkraster, skin, gui, trade_log
from PIL import ImageFont
_fc={}
def _pil(t,px,b=False):
    f=_fc.get((px,bool(b)))
    if f is None:
        f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"%("-Bold" if b else ""),int(px)); _fc[(px,bool(b))]=f
    return f.getbbox(t)[2]
skin.install_measurer(_pil)

TODAY = "2026-08-24"
gui.now_ist = lambda: dt.datetime(2026, 8, 24, 13, 12, 40)
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
with open(path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=trade_log.FIELDS); w.writeheader()
    w.writerow({"event":"OPEN","date":TODAY,"index":"NIFTY","status":"OPEN"})
    w.writerow({"event":"CLOSE","date":TODAY,"index":"NIFTY",
                "status":"CLOSED — T3 hit (full target reached)","pnl":4200,"lots":2})
    w.writerow({"event":"OPEN","date":TODAY,"index":"SENSEX","status":"OPEN"})
    w.writerow({"event":"CLOSE","date":TODAY,"index":"SENSEX",
                "status":"CLOSED — T3 hit (full target reached)","pnl":3170,"lots":2})
trade_log._log_path = lambda: path

root = tk.Tk(); app = gui.SignalApp(root)
app.popup_var.set(False); root.bell = lambda: None
app.lots_var.set("2")
app._day_cache = None; app._booked_cache = None
app.last_rec = {"index":"NIFTY","bias":"BULLISH","option_type":"CE",
  "suggested_strike":24500,"spot":24483.65,
  "index_targets":[24548.,24612.,24690.],"index_stop_loss":24402.,
  "premium_targets":[214.,246.,285.],"premium_stop_loss":152.,
  "premium_source":"live","live_ltp":186.4,
  "trend":{"label":"MODERATE UPTREND","direction":"UP","adx":24.1,"note":"momentum building","displacement_atr":1.4},
  "candles":None,"option_chain":None,"confidence":"Medium","score":3,"max_score":5}
import numpy as _np, pandas as _pd, signal_engine as _SE
_i = _pd.date_range(end="2026-08-24 13:00", periods=200, freq="15min")
_c = 24300 + _np.cumsum(_np.random.RandomState(9).randn(200) * 7 + 1.1)
_df = _pd.DataFrame({"Open": _c, "High": _c + 9, "Low": _c - 9, "Close": _c,
                     "Volume": 1000}, index=_i)
app.last_rec["trend"] = _SE.compute_market_trend(_df)
app.last_rec["candles"] = _df
app.spark_values = [float(v) for v in _df["Close"].tail(120)]
app._render_trend(app.last_rec["trend"])
app._render_trade_tracker(current_price=None)
app.history_text.insert("1.0","#1  10:12:55 IST  NIFTY 24400 CE  @ 168.20  -> CLOSED - T3 hit  |  Est. P&L: +Rs.4,200 (2 lots)\n")
app.history_text.insert("1.0","#2  12:41:03 IST  SENSEX 77100 PE  @ 210.40  -> CLOSED - T3 hit  |  Est. P&L: +Rs.3,170 (2 lots)\n")

sk = app.bridge.skin
sk.canvas = tkraster.Recorder(1570, 1002)
app.bridge._painted = False
app.bridge.flush()
print("badge:", sk.state["ticket"]["badge"])
tkraster.render(sk.canvas, path="/tmp/day_done.png")
print("wrote /tmp/day_done.png")
