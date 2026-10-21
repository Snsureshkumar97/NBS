#!/usr/bin/env python3
"""
oi_walls_study.py — sellers sitting just ahead: would the option chain have kept the tool out of its bad entries?
================================================================================
The user, 21 Oct 2026, after Nifty topped at 23,121 at 09:40 (the tool bought calls at 09:22) and fell to 22,650:
"how can we know the market will go up till there and over there if lot of sellers are sitting is there anyway from
option chain", then "do both" (a display, and this test). That morning the call OI at 23,100 / 23,200 was 67 / 80 lakh
just above the price, and +22 / +13 lakh more was written in the 19 minutes before the top.

THE DATA: Zerodha keeps no old option chains, so the only history is what record_options.py has saved since 10 Sep
2026 - every minute's OI of the strikes around the day's range, the two nearest expiries, on the server
(~/trading-tool-logs/option_history). The trades: every Indian ticket one live account's tool issued since then
(paper and live, every system), with its own recorded result, per lot (pnl / lots, before charges). A few weeks - a
first look, not proof: the three-year standard cannot be met until the history is months long (~Jan 2027).

FIXED BEFORE ANY RESULT WAS SEEN (21 Oct 2026). At the ticket's entry minute, on the nearest expiry, from the spot S:
  WALL     a call is held when the strike with the most call OI within 1.5% above S lies within 0.5% above S - a heavy
           seller level right in the way; a put, likewise with put OI below
  WRITING  a call is held when the call OI over the strikes within 0.5% above S rose 15% or more over the 15 minutes
           before entry (from 09:15 when the ticket is earlier than 09:30); a put, put OI within 0.5% below
  EITHER   either one
For each: the tickets it would have held against the ones it lets through - count, won, total and average per lot -
and the week-by-week difference, to see whether it is one lucky week. Nothing is built from this alone.

RESULT (21 Oct 2026; oi_walls_study_results.txt) - 162 tickets of one live account with option data at entry, 22 days
(10 Sep - 21 Oct 2026), per lot before charges; all of them +44,022:
  WALL     held 73 (45% - on Nifty 36 of 51: a heavy round strike is nearly always within 0.5%) that earned +9,737 -
           holding them would have cost money, better in 2 of 7 weeks; backwards on Nifty (+13,939 held vs -1,499 kept)
  WRITING  held 29 that roughly broke even (+279, +11 a ticket against +409 for the rest): -7,509 on Nifty / Bank Nifty /
           Sensex, but one Midcap winner (+7,788) cancels it; better in 1 of 7 weeks - a hint, not a rule
  EITHER   held 92 that earned +15,464 - DROP
21 Oct's Nifty call itself was not caught: the 23,100 call writing came 09:21-09:40, after the 09:22 entry. Built as a
live display only (feeds._sellers_ahead: the two heaviest call strikes above, put strikes below, their 15-minute change,
"adding"); re-test as an entry rule once the history is months long.

    python3 oi_walls_study.py [option_history_dir] [trades.csv]     (on the server, where both live)
"""
import glob
import os
import sys

import pandas as pd

HIST = os.path.expanduser("~/trading-tool-logs/option_history")
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
NEAR, AREA, WRITE, LOOKBACK = 0.005, 0.015, 0.15, 15


def load_day(path):
    d = pd.read_csv(path, parse_dates=["ts"])
    return d[d["kind"] == "OPT"]


def oi_table(o, t):
    """Each strike's call and put OI as of minute t (the last candle at or before it)."""
    x = o[o["ts"] <= t]
    if x.empty:
        return None
    return x.sort_values("ts").groupby(["strike", "opt"])["oi"].last().unstack("opt")


def flags(o, t, spot, side):
    """(WALL, WRITING, detail) for one ticket - from the chain at t and LOOKBACK minutes before."""
    now = oi_table(o, t)
    if now is None or not {"CE", "PE"} <= set(now.columns):
        return None
    t0 = max(t - pd.Timedelta(minutes=LOOKBACK), o["ts"].min())
    then = oi_table(o, t0)
    if side == "CE":
        col, area = "CE", now[(now.index > spot) & (now.index <= spot * (1 + AREA))]
        band = lambda tab: tab[(tab.index > spot) & (tab.index <= spot * (1 + NEAR))]
    else:
        col, area = "PE", now[(now.index < spot) & (now.index >= spot * (1 - AREA))]
        band = lambda tab: tab[(tab.index < spot) & (tab.index >= spot * (1 - NEAR))]
    if area.empty or area[col].isna().all():
        return None
    k = area[col].idxmax()
    wall = abs(k - spot) <= spot * NEAR
    b_now, b_then = band(now)[col].sum(), (band(then)[col].sum() if then is not None else 0)
    writing = bool(b_then > 0 and (b_now - b_then) / b_then >= WRITE)
    return wall, writing, {"wall_strike": float(k), "wall_oi": float(area[col].max()),
                           "band_chg": (b_now - b_then) / b_then if b_then else None}


def main(hist=HIST, trades_path=None):
    t = pd.read_csv(trades_path, dtype=str)
    t = t[(t["event"] == "CLOSE") & t["index"].isin(INDICES)].copy()
    t["when"] = pd.to_datetime(t["date"] + " " + t["time_ist"], errors="coerce")
    opens = pd.read_csv(trades_path, dtype=str)
    opens = opens[opens["event"] == "OPEN"].set_index("trade_id")
    rows = []
    days = {os.path.basename(p)[:10]: p for p in glob.glob(os.path.join(hist, "*.csv.gz"))}
    cache = {}
    for _, r in t.iterrows():
        day = r["date"]
        if day not in days or r["trade_id"] not in opens.index:
            continue
        o_ = opens.loc[r["trade_id"]]
        o_ = o_.iloc[0] if isinstance(o_, pd.DataFrame) else o_
        entry_t = pd.Timestamp(f"{day} {o_['time_ist']}")
        try:
            spot, lots, pnl = float(o_["entry_spot"]), float(r["lots"] or 1), float(r["pnl"])
        except (TypeError, ValueError):
            continue
        if day not in cache:
            cache[day] = load_day(days[day])
        o = cache[day]
        o = o[o["index"] == r["index"]]
        exps = sorted(e for e in o["expiry"].dropna().unique() if e >= day)
        if not exps:
            continue
        o = o[o["expiry"] == exps[0]].copy()
        o["ts"] = o["ts"].dt.tz_localize(None) if o["ts"].dt.tz is not None else o["ts"]
        f = flags(o, entry_t, spot, r["option_type"])
        if f is None:
            continue
        wall, writing, det = f
        rows.append({"day": day, "week": pd.Timestamp(day).strftime("%G-W%V"), "index": r["index"],
                     "system": r.get("system") or "rules", "side": r["option_type"], "per_lot": pnl / lots,
                     "WALL": wall, "WRITING": writing, "EITHER": wall or writing, **det})
    df = pd.DataFrame(rows)
    print("=" * 120)
    print(f" SELLERS JUST AHEAD - {len(df)} Indian tickets with option data at entry ({df['day'].min()} to {df['day'].max()},"
          f" {df['day'].nunique()} days), per lot before charges, ₹")
    print("=" * 120)
    line = lambda name, x: (f"   {name:34s} {len(x):>4} tickets  won {100 * (x['per_lot'] > 0).mean() if len(x) else 0:>4.0f}%"
                            f"  total ₹{x['per_lot'].sum():>+10,.0f}  avg ₹{x['per_lot'].mean() if len(x) else 0:>+8,.0f}")
    print(line("ALL TICKETS", df))
    for flag in ("WALL", "WRITING", "EITHER"):
        held, kept = df[df[flag]], df[~df[flag]]
        print(f" {flag}")
        print(line("would have been HELD", held))
        print(line("let through", kept))
        wk = df.groupby("week").apply(lambda g: -g.loc[g[flag], "per_lot"].sum())
        print("   week by week, what holding them would have changed: "
              + "  ".join(f"{w[-3:]} {v:+,.0f}" for w, v in wk.items())
              + f"   (better in {(wk > 0).sum()} of {len(wk)} weeks)")
        for k in INDICES:
            g = df[df["index"] == k]
            if len(g):
                h = g[g[flag]]
                print(f"      {k:11s} held {len(h):>3} of {len(g):>3}: their total ₹{h['per_lot'].sum():>+9,.0f}, "
                      f"the rest ₹{g.loc[~g[flag], 'per_lot'].sum():>+9,.0f}")
    print("\n Read a negative 'held' total as tickets the check would have saved you from, a positive one as winners it"
          " would have thrown away. A few weeks of data: a first look only.")
    return df


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args[0] if args else HIST, args[1] if len(args) > 1 else None)
