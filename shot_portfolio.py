import sys, os, csv, tempfile
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

today = "2026-08-24"
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
with open(path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=trade_log.FIELDS); w.writeheader()
    for idx, pnl in (("NIFTY",2100.),("NIFTY",-600.),("SENSEX",3170.),("BANKNIFTY",-450.)):
        w.writerow({"event":"CLOSE","date":today,"index":idx,"pnl":pnl,"lots":1,"lot_size":20})
trade_log._log_path = lambda: path

root = tk.Tk(); app = gui.SignalApp(root)
app.popup_var.set(False); root.bell = lambda: None
app.lots_var.set("2")
app._focus = "BANKNIFTY"
app.active_trade = {"index":"BANKNIFTY","option_type":"CE","strike":52000,
  "entry_time":"11:02:00","entry_spot":51900.,"entry_ltp":180.,"use_premium":True,
  "lot_size":30,"lots":2,"index_targets":[52100.,52200.,52300.],"index_sl":51800.,
  "premium_targets":[220.,260.,300.],"premium_sl":150.,
  "hit":{"T1":True,"T2":False,"T3":False},
  "hit_time":{"T1":"11:44:09","T2":None,"T3":None},
  "sl_hit":False,"sl_hit_time":None,"status":"OPEN"}
app.last_rec = {"option_chain":{"available":True,"expiry":today,"strikes":[
  {"strike":52000,"call_ltp":210.,"put_ltp":5.,"call_oi":1,"put_oi":1}]},"spot":51980.}
app.current = "BANKNIFTY"; app.index_var.set("BANKNIFTY"); app._focus = "BANKNIFTY"
app._render_trade_tracker(current_price=210.0)
app.history_text.insert("1.0", "#2  10:31:02 IST  NIFTY 24400 PE  @ 196.95  -> CLOSED - T3 hit  |  Est. P&L: +Rs.2,100 (1 lot)\n")
app.history_text.insert("1.0", "#3  11:58:40 IST  SENSEX 77100 PE  @ 210.40  -> CLOSED - T3 hit  |  Est. P&L: +Rs.3,170 (1 lot)\n")

for tag, W, H in [("pf1570",1570,1002), ("pf1180",1180,720)]:
    sk = app.bridge.skin
    sk.canvas = tkraster.Recorder(W, H)
    app.bridge._painted = False
    app.bridge.flush()
    tkraster.render(sk.canvas, path=f"/tmp/{tag}.png")
    print("wrote", tag)
