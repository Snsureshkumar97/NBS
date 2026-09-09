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

TODAY="2026-08-31"
gui.now_ist = lambda: dt.datetime(2026,8,31,11,42,5)
path=os.path.join(tempfile.mkdtemp(),"trades.csv")
with open(path,"w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=trade_log.FIELDS); w.writeheader()
    w.writerow({"event":"CLOSE","date":TODAY,"index":"SENSEX","pnl":3170,"lots":2,
                "status":"CLOSED — T3 hit"})
trade_log._log_path=lambda: path

root=tk.Tk(); app=gui.SignalApp(root)
app.popup_var.set(False); root.bell=lambda:None
app.lots_var.set("2")
app._day_cache=None; app._booked_cache=None
app.active_trade={"index":"NIFTY","option_type":"CE","strike":24500,
  "entry_time":"10:12:55","entry_spot":24402.,"entry_ltp":168.20,
  "use_premium":True,"lot_size":75,"lots":2,
  "index_targets":[24548.,24612.,24690.],"index_sl":24402.,
  "premium_targets":[214.0,246.0,285.0],"premium_sl":152.0,
  "hit":{"T1":True,"T2":False,"T3":False},
  "hit_time":{"T1":"10:41:02","T2":None,"T3":None},
  "sl_hit":False,"sl_hit_time":None,"status":"OPEN"}
app.last_rec={"option_chain":{"available":True,"strikes":[
  {"strike":24500,"call_ltp":196.40,"put_ltp":5.,"call_oi":1,"put_oi":1}]},
  "spot":24483.65}
app._render_trade_tracker(current_price=196.40)
app.history_text.insert("1.0","#1  09:58:11 IST  SENSEX 77100 PE  @ 210.40  -> CLOSED - T3 hit  |  Est. P&L: +Rs.3,170 (2 lots)\n")

for tag,W,H in [("lad1570",1570,1002),("lad1180",1180,720)]:
    sk=app.bridge.skin
    sk.canvas=tkraster.Recorder(W,H)
    app.bridge._painted=False
    app.bridge.flush()
    tkraster.render(sk.canvas,path=f"/tmp/{tag}.png")
    print("wrote",tag)
