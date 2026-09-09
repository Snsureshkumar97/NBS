"""Every level, both option types, both tracking modes."""
import sys
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import tkinter as tk, gui, theme as T

root = tk.Tk()
app = gui.SignalApp(root)
app.popup_var.set(False)
root.bell = lambda: None
app.root.bell = lambda: None

def fresh(option_type, use_premium):
    t = {"index": "SENSEX", "option_type": option_type, "strike": 77100,
         "entry_time": "09:18:31", "entry_spot": 77200.0, "entry_ltp": 200.0,
         "use_premium": use_premium, "lot_size": 20,
         # CE wants the index UP, PE wants it DOWN
         "index_targets": ([77300.0, 77400.0, 77500.0] if option_type == "CE"
                           else [77100.0, 77000.0, 76900.0]),
         "index_sl": 77100.0 if option_type == "CE" else 77300.0,
         "premium_targets": [260.0, 310.0, 360.0], "premium_sl": 150.0,
         "hit": {"T1": False, "T2": False, "T3": False},
         "hit_time": {"T1": None, "T2": None, "T3": None},
         "sl_hit": False, "sl_hit_time": None, "status": "OPEN",
         "trade_id": "x"}
    app.active_trade = t
    app.trade_history_count = 0
    return t

def marks():
    app.bridge.flush()
    return [(l[0], l[4], l[3]) for l in app.bridge.skin.state["ticket"]["levels"]]

fails = []
for option_type in ("CE", "PE"):
    for use_prem in (True, False):
        mode = "premium" if use_prem else "index"
        # --- walk price through T1, T2, T3 in order ---------------------
        t = fresh(option_type, use_prem)
        seq = ([260.0, 310.0, 360.0] if use_prem else
               ([77300.0, 77400.0, 77500.0] if option_type == "CE"
                else [77100.0, 77000.0, 76900.0]))
        for want, px in zip(("T1", "T2", "T3"), seq):
            app._check_trade_price(px)
            app._render_trade_tracker(current_price=px)
            got = marks()
            hit = [n for n, m, p in got if m]
            if want not in hit:
                fails.append(f"{option_type}/{mode}: {want} not marked at {px}")
            if not t["hit_time"][want]:
                fails.append(f"{option_type}/{mode}: {want} has no timestamp")
        if "T3 hit" not in t["status"]:
            fails.append(f"{option_type}/{mode}: T3 did not close the trade ({t['status']})")
        if app.badge_label.text.strip() != "TARGET HIT":
            fails.append(f"{option_type}/{mode}: badge is {app.badge_label.text!r}")
        print(f"  {option_type}/{mode:7s} T1 T2 T3 -> all marked, trade closed, badge TARGET HIT")

        # --- and the stop, from a clean ticket --------------------------
        t = fresh(option_type, use_prem)
        sl_px = (150.0 if use_prem else (77100.0 if option_type == "CE" else 77300.0))
        app._check_trade_price(sl_px)
        app._render_trade_tracker(current_price=sl_px)
        got = marks()
        if not got[3][1]:
            fails.append(f"{option_type}/{mode}: STOP not marked at {sl_px}")
        if "stop-loss" not in t["status"]:
            fails.append(f"{option_type}/{mode}: stop did not close the trade")
        if app.badge_label.text.strip() != "STOPPED OUT":
            fails.append(f"{option_type}/{mode}: badge is {app.badge_label.text!r}")
        col = app.bridge.skin.state["ticket"]["levels"][3][2]
        if col != T.DOWN:
            fails.append(f"{option_type}/{mode}: stop is {col}, not red")
        print(f"  {option_type}/{mode:7s} STOP     -> marked red, trade closed, badge STOPPED OUT")

# --- one level at a time: hitting T2 must not falsely mark T1 or T3 -----
t = fresh("PE", True)
app._check_trade_price(310.0)          # jumps straight past T1 to T2
app._render_trade_tracker(current_price=310.0)
got = marks()
hit = [n for n, m, p in got if m]
if hit != ["T1", "T2"]:
    fails.append(f"gap-up through T1+T2 marked {hit}, expected both")
print("  a gap straight through T1 and T2 marks both, not just the last")

t = fresh("PE", True)
app._check_trade_price(259.9)          # one tick short of T1
app._render_trade_tracker(current_price=259.9)
if any(m for n, m, p in marks()):
    fails.append("a level was marked before price reached it")
print("  one tick short of T1 marks nothing")

print()
if fails:
    for f in fails:
        print("FAIL:", f)
    raise SystemExit(1)
print("ALL LEVELS PASS — 3 targets + stop, CE and PE, premium and index")
