"""The ladder must stay inside its band, or fall back to cards."""
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

root=tk.Tk(); app=gui.SignalApp(root)
app.popup_var.set(False); root.bell=lambda:None
app.lots_var.set("2")
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
sk=app.bridge.skin

bad=[]; n=0; ladders=0; cards=0
for W in range(1100, 2700, 40):
    for H in range(620, 1500, 20):
        n+=1
        sk.canvas._w, sk.canvas._h = W, H
        app.bridge._painted=False
        try: app.bridge.flush()
        except Exception as e:
            bad.append((W,H,"CRASH "+str(e)[:60])); continue
        box=getattr(sk,"_levels_box",None)
        if not box: bad.append((W,H,"no band")); continue
        ly,ly1=box
        items=sk.canvas._items
        tags=[i for i in items if i.kind=="text" and
              any(str(i.opts.get("text","")).startswith(p)
                  for p in ("T1  ","T2  ","T3  ","STOP  "))]
        if tags:
            ladders+=1
            for i in tags:
                yy=i.coords[1]
                if not (ly - 20 <= yy <= ly1 + 20):
                    bad.append((W,H,f"ladder tag at y={yy:.0f} outside band {ly:.0f}..{ly1:.0f}"))
                    break
                if i.coords[0] > W:
                    bad.append((W,H,"ladder tag off the right edge")); break
            # the numbers above must not be sat on
            # Only the numbers ABOVE the band — the live-price marker inside
            # the ladder carries the same text as the NOW column and was being
            # counted as a collision with itself.
            stats=[i for i in items if i.kind=="text" and i.coords[1] < ly - 4 and
                   str(i.opts.get("text")) in ("168.20","196.40","+4,230","7.21 : 1")]
            if stats and tags and max(i.coords[1] for i in stats) > min(i.coords[1] for i in tags) - 14:
                bad.append((W,H,"ladder tags collide with the stats"))
        else:
            cards+=1
            lab=[i for i in items if i.kind=="text" and
                 str(i.opts.get("text")) in ("T2","T3","STOP")]
            if len(lab)<3:
                bad.append((W,H,f"neither ladder nor cards ({len(lab)} labels)"))

print("sizes:",n,"| drew the ladder:",ladders,"| fell back to cards:",cards)
print("problems:",len(bad))
for b in bad[:8]: print("   ",b)
if not bad: print("\nLADDER SWEEP PASSED")
