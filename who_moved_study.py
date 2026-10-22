#!/usr/bin/env python3
"""
who_moved_study.py — does "who moved" on the option chain tell which way the index goes next?
================================================================================
The user, 22 Oct 2026, after buyers went onto the option chain's drawing (feeds._buildup - buyers in / sellers in /
sellers out / buyers out at each strike over ~15 minutes, and the share of the changed contracts that bet on a rise):
"yes test it".

THE DATA: record_options.py's minute candles (premium and open interest of the strikes around the day's range, the
two nearest expiries) and the index's own minute candles, on the server since 10 Sep 2026 - a few weeks, so a first
look, not the three-year standard.

FIXED BEFORE ANY RESULT WAS SEEN (22 Oct 2026). For each index and day, every 15 minutes from 09:30 to 15:00 (the
forward windows do not overlap), on the nearest expiry: the lean exactly as the live page works it out -
feeds._buildup over the strikes within 2% of the index, the open interest and premium now against 15 minutes before.
  A. THE DIRECTION. The lean calls a rise at 60%+ and a fall at 40% or less (as the page's words do), nothing in
     between. Its calls against the index's move over the next 15 and 30 minutes, beside the plain alternative of
     MOMENTUM (the next 15 minutes go the way the last 15 went), and where the two disagree, which was right. The
     average next-15-minute move by lean band. It "tells the direction" only if, over 15 minutes: right 55% or more
     with the 95% interval above 50%, above 50% in both halves of the days, better than momentum, and under 50% on no
     index. Otherwise it stays a description and the page says what this found.
  B. THE TICKETS. Every Indian ticket one live account's tool issued since 10 Sep (paper and live, every system), per
     lot before charges, by the lean in the 15 minutes before its entry: AGREED (a call at 60%+, a put at 40% or
     less), AGAINST (the opposite), NEUTRAL. What holding everything but AGREED would have done, week by week. A first
     look only - nothing is built from it.

RESULT (22 Oct 2026, 26 days 10 Sep - 21 Oct; who_moved_study_results.txt) - IT DOES NOT TELL THE DIRECTION. Run first
with feeds.BUILDUP_MIN_OI at 3%, then again at the 1% the page uses since the same day (at 3% Nifty showed nothing around
midday); both runs say the same, these are the 1% figures (3% in brackets):
  A. 2,001 checks. The lean made a call at 93% of them (90%) and agreed with the last 15 minutes' own move 95% of the
     time: open interest rises nearly everywhere through the day and every premium moves with the index, so nearly
     every strike leans the way the index just went (953 checks at 0-20% rise, 858 at 80-100%, few between). Over the
     next 15 minutes it was right 49.7% (49.5%; 95%: 47.5-52.0%), momentum 49.0%; both halves of the days ~49-50%; no
     index above 52.2%. Over 30 minutes 52.4% (52.5%), the same as momentum (52.7%). In the 100 checks where the two
     disagreed: 55 to 45 over 15 minutes, 48 to 52 over 30 - noise. The average next move by lean band shows no order.
  B. 162 tickets, +44,022 per lot: AGREED 86 (+25,686, won 57%), NEUTRAL 43 (+7,370, won 35%), AGAINST 33 (+10,966,
     won 55%). Taking only AGREED would have cost 18,336, better in 1 of 7 weeks - DROP.
The page keeps it as a description of the last 15 minutes and its words say what this found (web_server buildupSay).

    python3 who_moved_study.py [option_history_dir] [trades.csv]     (on the server, where both live)
"""
import glob
import math
import os
import sys

import pandas as pd

HIST = os.path.expanduser("~/trading-tool-logs/option_history")
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
LOOK = pd.Timedelta(minutes=15)
UP, DOWN = 0.6, 0.4


def load_day(path):
    d = pd.read_csv(path, parse_dates=["ts"])
    d["ts"] = d["ts"].dt.tz_localize(None) if d["ts"].dt.tz is not None else d["ts"]
    return d


def at(series_df, t):
    """The last row at or before t, per (strike, opt) - a frame indexed by strike with CE/PE close and oi."""
    x = series_df[series_df["ts"] <= t]
    if x.empty:
        return None
    last = x.sort_values("ts").groupby(["strike", "opt"])[["close", "oi"]].last()
    return last


def chain_at(o, t, spot):
    """feeds._buildup's chain shape at minute t, strikes within 2.5% (the function itself keeps 2%)."""
    last = at(o, t)
    if last is None:
        return None, None
    strikes, past = [], {}
    for k in sorted({s for s, _ in last.index}):
        if abs(k - spot) > spot * 0.025:
            continue
        ce = last.loc[(k, "CE")] if (k, "CE") in last.index else None
        pe = last.loc[(k, "PE")] if (k, "PE") in last.index else None
        strikes.append({"strike": float(k),
                        "call_oi": float(ce["oi"]) if ce is not None and pd.notna(ce["oi"]) else None,
                        "put_oi": float(pe["oi"]) if pe is not None and pd.notna(pe["oi"]) else None,
                        "call_ltp": float(ce["close"]) if ce is not None else None,
                        "put_ltp": float(pe["close"]) if pe is not None else None})
    return {"strikes": strikes}, {s["strike"]: (s["call_oi"], s["put_oi"], s["call_ltp"], s["put_ltp"]) for s in strikes}


def lean_at(o, idx, t):
    """(lean_up or None, spot) at minute t from the chain then and 15 minutes before."""
    from feeds import _buildup
    sp = idx[idx.index <= t]
    if sp.empty:
        return None, None
    spot = float(sp.iloc[-1])
    now, _ = chain_at(o, t, spot)
    _, past = chain_at(o, t - LOOK, spot)
    if not now or not past:
        return None, spot
    bu = _buildup(now, spot, past, LOOK.total_seconds())
    return (bu or {}).get("lean_up"), spot


def day_frames(path, key):
    d = load_day(path)
    day = os.path.basename(path)[:10]
    idx = d[(d["kind"] == "IDX") & (d["index"] == key)].set_index("ts")["close"].sort_index()
    o = d[(d["kind"] == "OPT") & (d["index"] == key)]
    exps = sorted(e for e in o["expiry"].dropna().unique() if e >= day)
    if idx.empty or not exps:
        return None, None
    return o[o["expiry"] == exps[0]][["ts", "strike", "opt", "close", "oi"]], idx


def part_a(hist):
    rows = []
    for path in sorted(glob.glob(os.path.join(hist, "*.csv.gz"))):
        day = os.path.basename(path)[:10]
        for key in INDICES:
            o, idx = day_frames(path, key)
            if o is None:
                continue
            for t in pd.date_range(f"{day} 09:30", f"{day} 15:00", freq="15min"):
                lean, spot = lean_at(o, idx, t)
                if spot is None:
                    continue
                px = lambda tt: (lambda s: float(s.iloc[-1]) if not s.empty else None)(idx[idx.index <= tt])
                back, f15, f30 = px(t - LOOK), px(t + LOOK), px(t + 2 * LOOK) if t + 2 * LOOK <= pd.Timestamp(f"{day} 15:30") else None
                if back is None or f15 is None:
                    continue
                rows.append({"day": day, "index": key, "t": t, "lean": lean, "past": spot - back, "f15": f15 - spot,
                             "f30": (f30 - spot) if f30 is not None else None})
    return pd.DataFrame(rows)


def ci(k, n):
    """Wilson 95% interval for k right out of n."""
    if not n:
        return (0.0, 0.0)
    p, z = k / n, 1.96
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (c - h, c + h)


def hits(df, call_col, out_col):
    x = df[(df[call_col] != 0) & df[out_col].notna() & (df[out_col] != 0)]
    k = int((x[call_col] == x[out_col].apply(lambda v: 1 if v > 0 else -1)).sum())
    return k, len(x)


def report_a(a):
    a = a.copy()
    a["LEAN"] = a["lean"].apply(lambda v: 0 if v is None or pd.isna(v) else (1 if v >= UP else -1 if v <= DOWN else 0))
    a["MOMENTUM"] = a["past"].apply(lambda v: 1 if v > 0 else -1 if v < 0 else 0)
    days = sorted(a["day"].unique())
    half = set(days[:len(days) // 2])
    W = 120
    print("=" * W)
    print(f" A. DOES THE LEAN TELL WHICH WAY THE INDEX GOES NEXT? {len(a)} checks every 15 minutes, {len(days)} days "
          f"({days[0]} to {days[-1]}), four indices")
    print("=" * W)
    print(f"   the lean had something to say (any change counted) at {a['lean'].notna().mean():.0%} of checks; "
          f"called a rise (60%+) at {(a['LEAN'] == 1).mean():.0%}, a fall (40% or less) at {(a['LEAN'] == -1).mean():.0%}")
    print(f"   it agreed with the last 15 minutes' own move {((a['LEAN'] == a['MOMENTUM']) & (a['LEAN'] != 0)).sum() / max(1, (a['LEAN'] != 0).sum()):.0%}"
          " of the time it made a call")

    def line(name, df, col, out):
        k, n = hits(df, col, out)
        lo, hi = ci(k, n)
        return f"   {name:44s} right {k:>4} of {n:>4} = {100 * k / n if n else 0:5.1f}%   (95%: {100 * lo:4.1f}-{100 * hi:4.1f}%)", k, n
    out = {}
    for horizon in ("f15", "f30"):
        print(f"\n NEXT {'15' if horizon == 'f15' else '30'} MINUTES")
        for col in ("LEAN", "MOMENTUM"):
            s, k, n = line(f"{col} pooled", a, col, horizon)
            print(s)
            out[(col, horizon)] = (k, n)
        for name, g in (("first half of the days", a[a["day"].isin(half)]), ("second half", a[~a["day"].isin(half)])):
            s, k, n = line(f"LEAN, {name}", g, "LEAN", horizon)
            print(s)
            out[("LEAN", horizon, name)] = (k, n)
        for key in INDICES:
            s, k, n = line(f"LEAN, {key}", a[a["index"] == key], "LEAN", horizon)
            print(s)
            out[("LEAN", horizon, key)] = (k, n)
        dis = a[(a["LEAN"] != 0) & (a["MOMENTUM"] != 0) & (a["LEAN"] != a["MOMENTUM"])]
        k, n = hits(dis, "LEAN", horizon)
        print(f"   where the lean and the last 15 minutes DISAGREED ({n}): the lean was right {k}, momentum {n - k}")
    print("\n AVERAGE INDEX MOVE OVER THE NEXT 15 MINUTES BY LEAN BAND (points; the indices differ in size, so also in"
          " basis points of the index)")
    bands = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in bands:
        g = a[a["lean"].notna() & (a["lean"] >= lo) & (a["lean"] < hi)]
        print(f"   lean {int(lo * 100):>3}-{min(100, int(hi * 100)):>3}% rise   {len(g):>4} checks   up next {100 * (g['f15'] > 0).mean() if len(g) else 0:5.1f}%"
              f"   average move {g['f15'].mean() if len(g) else 0:+7.1f} pts")
    g = a[a["lean"].isna()]
    print(f"   no change counted        {len(g):>4} checks   up next {100 * (g['f15'] > 0).mean() if len(g) else 0:5.1f}%")
    # the verdict, by the rule fixed above
    k, n = out[("LEAN", "f15")]
    lo, _ = ci(k, n)
    km, nm = out[("MOMENTUM", "f15")]
    halves = [out[("LEAN", "f15", h)] for h in ("first half of the days", "second half")]
    per = [out[("LEAN", "f15", key)] for key in INDICES]
    ok = (n and k / n >= 0.55 and lo > 0.5 and all(kk / nn > 0.5 for kk, nn in halves if nn)
          and (k / n) > (km / nm if nm else 0) and all(kk / nn >= 0.5 for kk, nn in per if nn))
    print(f"\n VERDICT (A): {'TELLS THE DIRECTION' if ok else 'DOES NOT TELL THE DIRECTION'} - over 15 minutes the lean was right"
          f" {100 * k / n if n else 0:.1f}% (95% from {100 * lo:.1f}%), momentum {100 * km / nm if nm else 0:.1f}%")
    return ok


def part_b(hist, trades_path):
    t = pd.read_csv(trades_path, dtype=str)
    closes = t[(t["event"] == "CLOSE") & t["index"].isin(INDICES)]
    opens = t[t["event"] == "OPEN"].drop_duplicates("trade_id").set_index("trade_id")
    days = {os.path.basename(p)[:10]: p for p in glob.glob(os.path.join(hist, "*.csv.gz"))}
    frames, rows = {}, []
    for _, r in closes.iterrows():
        day = r["date"]
        if day not in days or r["trade_id"] not in opens.index:
            continue
        o_ = opens.loc[r["trade_id"]]
        try:
            lots, pnl = float(r["lots"] or 1), float(r["pnl"])
        except (TypeError, ValueError):
            continue
        key = (day, r["index"])
        if key not in frames:
            frames[key] = day_frames(days[day], r["index"])
        o, idx = frames[key]
        if o is None:
            continue
        lean, _ = lean_at(o, idx, pd.Timestamp(f"{day} {o_['time_ist']}"))
        side = r["option_type"]
        grp = ("NEUTRAL" if lean is None or DOWN < lean < UP else
               "AGREED" if (side == "CE") == (lean >= UP) else "AGAINST")
        rows.append({"day": day, "week": pd.Timestamp(day).strftime("%G-W%V"), "index": r["index"], "side": side,
                     "lean": lean, "group": grp, "per_lot": pnl / lots})
    return pd.DataFrame(rows)


def report_b(b):
    W = 120
    print("\n" + "=" * W)
    print(f" B. THE TOOL'S OWN TICKETS BY THE LEAN BEFORE ENTRY - {len(b)} Indian tickets of one live account "
          f"({b['day'].min()} to {b['day'].max()}), per lot before charges, ₹")
    print("=" * W)
    line = lambda name, x: (f"   {name:34s} {len(x):>4} tickets  won {100 * (x['per_lot'] > 0).mean() if len(x) else 0:>4.0f}%"
                            f"  total ₹{x['per_lot'].sum():>+10,.0f}  avg ₹{x['per_lot'].mean() if len(x) else 0:>+8,.0f}")
    print(line("ALL", b))
    for g in ("AGREED", "NEUTRAL", "AGAINST"):
        print(line(g, b[b["group"] == g]))
    held = b[b["group"] != "AGREED"]
    wk = b.groupby("week").apply(lambda g: -g.loc[g["group"] != "AGREED", "per_lot"].sum())
    print(f"   taking only AGREED would have changed the total by ₹{-held['per_lot'].sum():+,.0f}; week by week: "
          + "  ".join(f"{w[-3:]} {v:+,.0f}" for w, v in wk.items()) + f"   (better in {(wk > 0).sum()} of {len(wk)} weeks)")
    for key in INDICES:
        g = b[b["index"] == key]
        if len(g):
            print(f"      {key:11s} " + "   ".join(f"{x} {len(g[g['group'] == x]):>3}: ₹{g.loc[g['group'] == x, 'per_lot'].sum():>+8,.0f}"
                                                 for x in ("AGREED", "NEUTRAL", "AGAINST")))


def main(hist=HIST, trades_path=None):
    a = part_a(hist)
    report_a(a)
    if trades_path:
        report_b(part_b(hist, trades_path))
    return a


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args[0] if args else HIST, args[1] if len(args) > 1 else None)
