"""Why does the ladder appear for a second and then go back to cards?"""
import sys
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, gui, skin
from PIL import ImageFont
_fc={}
def _pil(t,px,b=False):
    f=_fc.get((px,bool(b)))
    if f is None:
        f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"%("-Bold" if b else ""),int(px)); _fc[(px,bool(b))]=f
    return f.getbbox(t)[2]
skin.install_measurer(_pil)

root=tk.Tk(); app=gui.SignalApp(root); app.popup_var.set(False); root.bell=lambda:None
sk=app.bridge.skin
sk.canvas._w, sk.canvas._h = 1570, 1002

def form():
    app.bridge._painted=False
    app.bridge.flush()
    t=sk.state["ticket"]
    txt=[str(i.opts.get("text","")) for i in sk.canvas._items if i.kind=="text"]
    tags=[x for x in txt if x.startswith(("T1  ","T2  ","T3  ","STOP  "))]
    skeleton=any("Levels appear here" in x for x in txt)
    box=getattr(sk,"_levels_box",(0,0))
    form = "LADDER" if tags else ("LADDER (empty)" if skeleton else "cards")
    return form, box[1]-box[0]

BASE = {"index":"NIFTY","bias":"BULLISH","option_type":"CE","suggested_strike":24500,
        "spot":24483.65,"index_targets":[24548.,24612.,24690.],"index_stop_loss":24402.,
        "premium_targets":[214.,246.,285.],"premium_stop_loss":152.,
        "premium_source":"live","live_ltp":196.40,"candles":None,"option_chain":None}

print("STEP 1  a live signal arrives (preview)")
app.last_rec=dict(BASE)
app._render_trade_tracker(current_price=None)
print("        ->", form())

print("\nSTEP 2  next cycle: indicators stop agreeing (the usual case)")
app.last_rec=dict(BASE, bias="NEUTRAL", blockers=[
    "Indicators not agreeing enough (leaning bullish). only 2 agree, need at "
    "least 3 (2 of 5 indicators voted); undecided (not counted against you): "
    "Trend, VWAP, PCR."])
app._render_trade_tracker(current_price=None)
print("        ->", form())

print("\nSTEP 3  a signal that is real but has no room (NOT WORTH IT)")
app.last_rec=dict(BASE, bias="NEUTRAL", not_worth_it=True, raw_bias="bullish",
                  reach_points=40, risk_points=90, reach_reason="OI wall",
                  blocked_reason="no_room")
app._render_trade_tracker(current_price=None)
print("        ->", form())

print("\nSTEP 4  a long 'waiting because' sub-line WITH levels still present")
app.last_rec=dict(BASE, blockers=["x"*220])
app._render_trade_tracker(current_price=None)
print("        ->", form())
