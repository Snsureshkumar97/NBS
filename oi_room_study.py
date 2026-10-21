#!/usr/bin/env python3
"""
oi_room_study.py — room to run limited by the nearest heavy option-seller level: would it have kept the bad trades out?
================================================================================
The user, 21 Oct 2026, after the "sellers above / support below" display: "can that data be used in room to run to
know how far can market go", then "yes run it". Room to run (signal_engine.compute_reachability) already has an OI wall
among its limits, but it is the heaviest strike of the WHOLE expiry and is never let closer than half the expected
move - so the level right in the way (21 Oct: 67 lakh calls at 23,100, 28 points above the 09:22 entry) never counted.

THE DATA: the server's recorded option history (record_options.py, from 10 Sep 2026) and one live account's own
tickets (the tool's real decisions, paper and live, with their recorded results, per lot before charges) - a few
weeks: a first look, not proof (oi_walls_study.py's same data).

FIXED BEFORE ANY RESULT WAS SEEN (21 Oct 2026). Rules tickets only (room to run gates only those; the Trend Rider and the
TradingView rating set their own targets). At the entry minute, the nearest expiry, from the entry spot S, in the
trade's direction, within 1.5%: the two strikes with the most OI (calls above for a call, puts below for a put).
  NEAREST    the room becomes min(the tool's own room at entry, the distance to the nearer of the two)
  HEAVIEST   the room becomes min(the tool's own room, the distance to the heavier of the two)
A ticket FAILS when its new room is less than its stop distance (under 1:1 - the live reward:risk minimum,
MIN_REWARD_RISK_T3 1.0 for the Indian indices). The failing tickets' own results against the passing ones', per index
and week by week. Nothing is built from this alone.

RESULT (21 Oct 2026; oi_room_study_results.txt) - 146 rules tickets of one live account with option data, 22 days
(10 Sep - 21 Oct 2026), per lot before charges; all of them +30,893. (A first run read tickets from before the `system`
column as not the rules and saw only 50 of them - fixed: a blank system is the rules.)
  NEAREST   holds 101 of 146: won 49%, -8,747 in all; the 45 left +39,640 - more profit, but 7 trades in 10 gone, and it
            costs on Nifty (+2,791 held) and Sensex (+858 held); better in 4 of 7 weeks
  HEAVIEST  holds 36 of 146: won 36%, -5,959 in all - negative on every index (Nifty -3,032, Bank Nifty -2,174, Sensex
            -483, Midcap -270); the 110 left +36,852; better in 4 of 7 weeks - not proven (STEADY needs 60%)
21 Oct's own Nifty call passes HEAVIEST (23,200, 128 points against a 114-point stop); the nearer 23,100 stopped it.
Built as a switch per index in Trade settings, OFF by default (config.SELLERS_ROOM_*, tickets._sellers_room_hold) - the
user, asked, left it to the recommendation. Re-run as the recorded history grows.

    python3 oi_room_study.py [option_history_dir] [trades.csv]     (on the server, where both live)
"""
import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oi_walls_study as ows                                               # noqa: E402

AREA, NEED = 0.015, 1.0


def levels(o, t, spot, side):
    """(nearest, heaviest) distance in points to the two heaviest strikes within AREA in the trade's direction."""
    now = ows.oi_table(o, t)
    if now is None or not {"CE", "PE"} <= set(now.columns):
        return None
    if side == "CE":
        area = now[(now.index > spot) & (now.index <= spot * (1 + AREA))]["CE"].dropna()
    else:
        area = now[(now.index < spot) & (now.index >= spot * (1 - AREA))]["PE"].dropna()
    if area.empty:
        return None
    top = area.sort_values(ascending=False).head(2)
    d = {k: abs(k - spot) for k in top.index}
    return min(d.values()), d[top.index[0]], float(top.index[0])


def main(hist=ows.HIST, trades_path=None):
    t = pd.read_csv(trades_path, dtype=str)
    closes = t[(t["event"] == "CLOSE") & t["index"].isin(ows.INDICES)]
    opens = t[t["event"] == "OPEN"].drop_duplicates("trade_id").set_index("trade_id")
    days = {os.path.basename(p)[:10]: p for p in glob.glob(os.path.join(hist, "*.csv.gz"))}
    cache, rows = {}, []
    for _, r in closes.iterrows():
        day, tid = r["date"], r["trade_id"]
        if day not in days or tid not in opens.index:
            continue
        o_ = opens.loc[tid]
        sysv = o_.get("system")
        if isinstance(sysv, str) and sysv and sysv != "rules":     # blank on tickets from before the column: the rules
            continue
        try:
            spot, risk, room = float(o_["entry_spot"]), float(o_["risk_points"]), float(o_["reach_points"])
            lots, pnl = float(r["lots"] or 1), float(r["pnl"])
        except (TypeError, ValueError):
            continue
        if day not in cache:
            cache[day] = ows.load_day(days[day])
        o = cache[day]
        o = o[o["index"] == r["index"]]
        exps = sorted(e for e in o["expiry"].dropna().unique() if e >= day)
        if not exps or risk <= 0:
            continue
        o = o[o["expiry"] == exps[0]].copy()
        o["ts"] = o["ts"].dt.tz_localize(None) if o["ts"].dt.tz is not None else o["ts"]
        lv = levels(o, pd.Timestamp(f"{day} {o_['time_ist']}"), spot, r["option_type"])
        if lv is None:
            continue
        near, heavy, k = lv
        rows.append({"day": day, "week": pd.Timestamp(day).strftime("%G-W%V"), "index": r["index"], "per_lot": pnl / lots,
                     "risk": risk, "room": room, "near": near, "heavy": heavy,
                     "NEAREST": min(room, near) / risk < NEED, "HEAVIEST": min(room, heavy) / risk < NEED})
    df = pd.DataFrame(rows)
    print("=" * 120)
    print(f" ROOM TO RUN LIMITED BY THE NEAREST HEAVY SELLER LEVEL - {len(df)} rules tickets with option data "
          f"({df['day'].min()} to {df['day'].max()}, {df['day'].nunique()} days), per lot before charges, ₹")
    print("=" * 120)
    line = lambda name, x: (f"   {name:34s} {len(x):>4} tickets  won {100 * (x['per_lot'] > 0).mean() if len(x) else 0:>4.0f}%"
                            f"  total ₹{x['per_lot'].sum():>+10,.0f}  avg ₹{x['per_lot'].mean() if len(x) else 0:>+8,.0f}")
    print(line("ALL RULES TICKETS", df))
    print(f"   the tool's own room at entry: median {df['room'].median():,.0f} pts; the nearest heavy level: median "
          f"{df['near'].median():,.0f} pts; the stop: median {df['risk'].median():,.0f} pts")
    for v in ("NEAREST", "HEAVIEST"):
        fail, ok = df[df[v]], df[~df[v]]
        print(f" {v}")
        print(line("would FAIL (under 1:1) - held", fail))
        print(line("would still pass", ok))
        wk = df.groupby("week").apply(lambda g: -g.loc[g[v], "per_lot"].sum())
        print("   week by week, what holding them would have changed: "
              + "  ".join(f"{w[-3:]} {x:+,.0f}" for w, x in wk.items()) + f"   (better in {(wk > 0).sum()} of {len(wk)} weeks)")
        for k in ows.INDICES:
            g = df[df["index"] == k]
            if len(g):
                h = g[g[v]]
                print(f"      {k:11s} held {len(h):>3} of {len(g):>3}: their total ₹{h['per_lot'].sum():>+9,.0f}, "
                      f"the rest ₹{g.loc[~g[v], 'per_lot'].sum():>+9,.0f}")
    print("\n A negative 'held' total = losers the limit would have kept you out of; positive = winners it would have thrown"
          " away. A few weeks of data: a first look only.")
    return df


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args[0] if args else ows.HIST, args[1] if len(args) > 1 else None)
