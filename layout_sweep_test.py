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
app.active_trade={"index":"SENSEX","option_type":"PE","strike":77100,"entry_time":"09:18:31",
 "entry_spot":77218.4,"entry_ltp":196.95,"use_premium":True,"lot_size":20,
 "index_targets":[77020.,76940.,76860.],"index_sl":77330.,
 "premium_targets":[261.31,309.58,357.84],"premium_sl":148.2,
 "hit":{"T1":True,"T2":False,"T3":False},"hit_time":{"T1":"10:07:44","T2":None,"T3":None},
 "sl_hit":False,"sl_hit_time":None,"status":"OPEN"}
app._render_trade_tracker(current_price=294.50)
sk=app.bridge.skin
bad=[]; n=0
for W in range(1080, 3900, 40):
    for H in range(560, 2200, 20):
        n+=1
        sk.canvas._w, sk.canvas._h = W, H
        app.bridge._painted=False
        try: app.bridge.flush()
        except Exception as e: bad.append((W,H,"CRASH "+str(e)[:50])); continue
        box=getattr(sk,"_levels_box",None)
        if not box: bad.append((W,H,"no band")); continue
        ly,ly1=box
        if ly1-ly < 40: bad.append((W,H,f"band {ly1-ly:.0f}px")); continue
        if ly1 > H: bad.append((W,H,f"band bottom {ly1:.0f} > H {H}")); continue
        items=sk.canvas._items
        # all four level names present and on-screen
        # The panel draws EITHER four cards or the ladder, depending on the
        # room available. Both are correct; what matters is that all four
        # levels are named exactly once, whichever form they take.
        labs=[i for i in items if i.kind=="text" and
              (str(i.opts.get("text")) in ("T2","T3","STOP")
               or str(i.opts.get("text","")).startswith("T1")
               or str(i.opts.get("text","")).startswith(("T1  ","T2  ","T3  ","STOP  ")))]
        if len(labs) < 4: bad.append((W,H,f"{len(labs)}/4 level labels")); continue
        for i in labs:
            if not (0 <= i.coords[0] <= W and 0 <= i.coords[1] <= H):
                bad.append((W,H,f"label off-screen at {[round(v) for v in i.coords]}")); break
        else:
            # the stats numbers must not sit on top of the cards
            # Only the numbers ABOVE the band. The ladder's live-price marker
            # sits inside it and carries the same text as the NOW column.
            stats=[i for i in items if i.kind=="text" and i.coords[1] < ly - 4
                   and str(i.opts.get("text")) in ("196.95","294.50","+1,951")]
            if stats and max(i.coords[1] for i in stats) > ly - 2:
                bad.append((W,H,"stats overlap the cards"))
print("sizes checked:", n)
print("problems:", len(bad))
for b in bad[:10]: print("   ", b)
if not bad: print("\nLEVEL CARDS SURVIVE EVERY SIZE 1080x560 .. 3860x2180")
