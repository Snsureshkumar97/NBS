#!/usr/bin/env python3
"""
order_block_site_study.py — the order-block.com method, backtested as its own guides describe it
================================================================================
The user, 27 Oct 2026: "can we backtest this strategy", and then "but if backtest fails how are they making money
everyday profits i have been following them". Their guides (the playbook, "What is an order block?", "Let the zone come to
you") describe one method; earlier studies tested only parts of it inside the tool's own systems (order_block_study.py:
order blocks as a filter; retest_entry_study: waiting for a retest; breadth_study: the market's side). This is the
method itself, standalone.

THE RULES, FIXED FROM THEIR GUIDES BEFORE ANY RESULT WAS SEEN (27 Oct 2026), on FINISHED 5-minute candles:
  structure: a swing high is a high with lower highs on both sides (SWING candles each side, so it is known SWING
    candles later); the first 15 minutes' high and low count as structure too
  break of structure: a 5-minute close above the latest swing high (or the first-15-minute high) - "only a close counts" -
    by a candle that "travels further than a normal candle" (its range at least the 5-minute ATR(14))
  the order block: the last red candle before that break (the last green one, for a fall); the zone is its range: Z its
    top (a bullish block; the bottom for a bearish one) and S1 its far edge
  the zone is broken when a finished 5-minute candle closes beyond S1; only its FIRST retest is traded
  entry: the first later candle that comes back to Z - filled at Z, or at the open if it opens beyond Z - between 09:20
    and 15:00, on the market's side only ("a bullish zone on a day when most stocks fall swims against the tide"): for
    an index, the index above its previous close for a call and below it for a put; for a stock, more of the stocks up
    than down at that candle, and the stock among the MOST_STRONG strongest on that side (its move against the average of
    its own last 50 sessions - their strength score)
  target: the liquidity the move left above - the highest high between the break and the retest (the lowest low for a
    fall), the nearest high no candle has closed above; at least once the risk (Z to S1) away, or the setup is skipped
    ("the move may stall there, leaving too little room")
  stop: a finished 5-minute candle closing beyond S1 ("a 5-minute close beyond it ends the idea"), out at that close
  out at the day's close (15:15) at the latest; one position at a time per instrument; a new zone after every break
  INDICES (Nifty, Bank Nifty and Sensex from Aug 2020, Midcap Select from Sep 2023): the at-the-money option of the real
    expiry, priced as every study prices it (indian_strategies_study.opt_net: Black-Scholes on the day's sigma, slippage
    0.25%, Zerodha's charges), per lot - against the tool's own rules, and the rules with the big candle (as live now)
  STOCKS (the 56 market-map F&O stocks, Sep 2023 - Oct 2026; today's members, so survivorship-biased in their favour):
    the move itself in % of price per trade (a future or the stock), after 0.06% for charges and slippage both ways
  SENSITIVITY (shown, judged only as a group): no market-side filter; a fixed 2R target; the stop on a touch of S1, not
    a close; swings of 3 candles
Periods: to 15 Aug 2025, then held out (as every study).

RESULT (27 Oct 2026; order_block_site_study_results.txt) - IT LOSES, ON THE INDICES AND ON THE STOCKS IT IS MADE FOR.
  Indices, per lot (to 15 Aug 2025 / held-out), their method against the tool's rules + big candle as live now:
    Nifty -40,297 / -22,928 against +337,763 / +71,011      Bank Nifty -84,438 / -55,053 against +348,397 / +42,080
    Sensex -35,792 / +31,561 against +325,348 / +84,551     Midcap (from Sep 2023) -63,311 / -54,261 against +222,376 / +123,538
    about 45% won; better than the tool in 0-2 of 13 half-years; every one of the five versions loses in the first period
  Stocks (56, Sep 2023 - Oct 2026, the strongest on the market's side): 6,881 trades, 49% won, +0.032% a trade before
    costs and -0.028% after 0.06% - total -112% / -79% (summed % per trade), PF 0.82-0.84; every version loses after costs
  What their pages show - zones drawn on a finished chart, the setups that held - is the part that wins.

    HOME=<scratch with the 6-year 15-minute history> OB5M=<folder with the 5-minute candles> /usr/bin/python3 order_block_site_study.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
MOST_STRONG = 10                 # their board shows 20 of 200+ per side; 10 of our 56
COST_PCT = 0.06
BAR = pd.Timedelta(minutes=5)
VARIANTS = {   # name -> (swing, side filter, target "liq" or "2R", stop "close" or "touch")
    "AS THEIR GUIDES": (2, True, "liq", "close"),
    "no market side": (2, False, "liq", "close"),
    "fixed 2R target": (2, True, "2R", "close"),
    "stop on a touch": (2, True, "liq", "touch"),
    "swings of 3": (3, True, "liq", "close"),
}


def load_5m(path):
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Kolkata")
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def trades_for(df, variant, side_ok=None):
    """[{i, j, side, entry, exit, how, when, out}] for one instrument's 5-minute candles. side_ok(i, side) -> bool is the
    market-side (and, for a stock, strength) gate at the entry candle; None lets every side through."""
    import indicators as ind
    swing, use_side, target_mode, stop_mode = variant
    o, h, l, c = (df[x].to_numpy(float) for x in ("Open", "High", "Low", "Close"))
    atr = ind.atr(df, 14).shift(1).to_numpy(float)
    t = df.index
    tod = np.array([x.hour * 60 + x.minute for x in t])
    day = np.array([x.date() for x in t])
    starts = np.r_[0, np.where(day[1:] != day[:-1])[0] + 1]
    prev_close = {}
    out = []
    for n, s0 in enumerate(starts):
        e0 = starts[n + 1] if n + 1 < len(starts) else len(df)
        if n:
            prev_close[day[s0]] = c[s0 - 1]
        pc = prev_close.get(day[s0])
        if e0 - s0 < 6 or pc is None:
            continue
        up_lvl, dn_lvl = h[s0:s0 + 3].max(), l[s0:s0 + 3].min()        # the first 15 minutes
        zl = zs = None                                                  # the live bullish / bearish zone
        busy = -1
        for i in range(s0 + 3, e0):
            k = i - swing                                               # a swing confirmed at this candle
            if k - swing >= s0:
                if h[k] > h[k - swing:k].max() and h[k] > h[k + 1:k + swing + 1].max():
                    up_lvl = h[k]
                if l[k] < l[k - swing:k].min() and l[k] < l[k + 1:k + swing + 1].min():
                    dn_lvl = l[k]
            # a live zone: broken by a close beyond its far edge; the liquidity it left (the extreme since the break)
            for z, bull in ((zl, True), (zs, False)):
                if z is None:
                    continue
                if (c[i] < z["S1"]) if bull else (c[i] > z["S1"]):
                    z["dead"] = True
            zl = None if zl is not None and zl.get("dead") else zl
            zs = None if zs is not None and zs.get("dead") else zs
            # a retest while a position is open still uses the zone up: only its FIRST retest is ever traded
            if i <= busy:
                for z, bull in ((zl, True), (zs, False)):
                    if z is not None and i > z["bos"] and ((l[i] <= z["Z"]) if bull else (h[i] >= z["Z"])):
                        z["used"] = True
                zl = None if zl is not None and zl.get("used") else zl
                zs = None if zs is not None and zs.get("used") else zs
            # the first retest of a live zone (from the candle after its break), when nothing is open
            if i > busy and 9 * 60 + 20 <= tod[i] and tod[i] + 5 <= 15 * 60:
                for z, side in ((zl, "CE"), (zs, "PE")):
                    if z is None or i <= z["bos"]:
                        continue
                    bull = side == "CE"
                    touched = (l[i] <= z["Z"]) if bull else (h[i] >= z["Z"])
                    if not touched:
                        continue
                    z["used"] = True
                    entry = min(o[i], z["Z"]) if bull else max(o[i], z["Z"])
                    risk = (entry - z["S1"]) if bull else (z["S1"] - entry)
                    if risk <= 0:
                        break
                    tgt = (entry + 2 * risk if bull else entry - 2 * risk) if target_mode == "2R" else z["liq"]
                    room = (tgt - entry) if bull else (entry - tgt)
                    if room < risk or (use_side and side_ok is not None and not side_ok(i, side)):
                        break
                    # the walk: the stop on a close (or a touch) beyond S1, checked first; the target as a resting order
                    px, j, how = None, None, None
                    for m in range(i, e0):
                        if tod[m] + 5 > 15 * 60 + 15:
                            px, j, how = c[m - 1], m - 1, "close"
                            break
                        hit_stop = ((c[m] < z["S1"]) if bull else (c[m] > z["S1"])) if stop_mode == "close" else \
                                   ((l[m] <= z["S1"]) if bull else (h[m] >= z["S1"]))
                        if hit_stop:
                            px, j, how = (c[m] if stop_mode == "close" else z["S1"]), m, "stop"
                            break
                        if m > i and ((h[m] >= tgt) if bull else (l[m] <= tgt)):
                            px, j, how = tgt, m, "target"
                            break
                    if px is None:
                        px, j, how = c[e0 - 1], e0 - 1, "close"
                    out.append({"i": i, "j": j, "side": side, "entry": float(entry), "exit": float(px), "how": how,
                                "when": t[i] + BAR, "out": t[j] + BAR})
                    busy = j
                    break
                zl = None if zl is not None and zl.get("used") else zl
                zs = None if zs is not None and zs.get("used") else zs
            # a break of structure on this close, by a candle bigger than a normal one: a new zone from the last
            # opposite candle before it
            a = atr[i]
            if not (a == a) or a <= 0 or h[i] - l[i] < a:
                continue
            if c[i] > up_lvl:
                ob = next((m for m in range(i - 1, s0 - 1, -1) if c[m] < o[m]), None)
                if ob is not None:
                    zl = {"Z": h[ob], "S1": l[ob], "bos": i, "liq": h[i]}
                up_lvl = math.inf
            elif c[i] < dn_lvl:
                ob = next((m for m in range(i - 1, s0 - 1, -1) if c[m] > o[m]), None)
                if ob is not None:
                    zs = {"Z": l[ob], "S1": h[ob], "bos": i, "liq": l[i]}
                dn_lvl = -math.inf
            # the liquidity a live zone leaves: the extreme reached since its break
            if zl is not None and i > zl["bos"]:
                zl["liq"] = max(zl["liq"], h[i])
            if zs is not None and i > zs["bos"]:
                zs["liq"] = min(zs["liq"], l[i])
    return out


def _index_job(job):
    key, vname = job
    import backtest_intraday as bt
    import index_candidates_study as ics
    import indian_strategies_study as iss
    import pro_study as ps
    import regime_study as rs
    ps.SLIP = 0.0025
    if key == "MIDCPNIFTY":
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    d15 = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(d15, rs.load_vix(), nrv)["sigma"]
    sig_day = sig.groupby(sig.index.date).first().to_dict()
    days = set(d15.index.date)
    df = load_5m(os.path.join(os.environ["OB5M"], f"{key}_5m.csv"))
    df = df[df.index.date >= min(days)]
    c = df["Close"].to_numpy(float)
    day = np.array([x.date() for x in df.index])
    first = np.r_[True, day[1:] != day[:-1]]
    last_close = pd.Series(c, index=df.index).groupby(day).transform("last").shift(1)
    pcs = pd.Series(np.where(first, last_close, np.nan), index=df.index).ffill().to_numpy()
    side_ok = lambda i, side: (c[i] > pcs[i]) if side == "CE" else (c[i] < pcs[i])
    rows = []
    for tr in trades_for(df, VARIANTS[vname], side_ok):
        s = sig_day.get(tr["when"].date())
        if s is None or not (s == s) or s <= 0:
            continue
        net = iss.opt_net(key, days, tr["when"], tr["out"], tr["entry"], tr["exit"], tr["side"], float(s))
        if net is not None:
            rows.append({"when": tr["when"], "exit_time": tr["out"], "net": net, "side": tr["side"], "how": tr["how"]})
    return job, rows


def _tool_job(job):
    key, what = job
    if what == "rules":
        import order_block_study as ob
        return job, ob.rules_rows(key)
    import big_candle_study as bcs
    return job, bcs.run(key, ("K1.5 end 2R", 1.5, True, "end", "2R"))


def slot(rules, bc):
    ev = sorted([(pd.Timestamp(r["when"]), pd.Timestamp(r.get("exit_time") or r["when"]), "r", r) for r in rules] +
                [(pd.Timestamp(r["when"]), pd.Timestamp(r["out"]), "b", r) for r in bc], key=lambda x: (x[0], x[2] != "r"))
    out, busy = [], None
    for a, b, kind, r in ev:
        if busy is not None and a < busy:
            continue
        out.append(r)
        busy = b
    return out


def stocks(vname):
    """Every market-map stock, the strongest on the market's side, % per trade after COST_PCT."""
    folder = os.path.join(os.environ["OB5M"], "stocks")
    data = {f[:-7]: load_5m(os.path.join(folder, f)) for f in sorted(os.listdir(folder)) if f.endswith("_5m.csv")}
    closes = pd.DataFrame({s: d["Close"] for s, d in data.items()}).sort_index()
    day = pd.Series(closes.index.date, index=closes.index)
    daily = closes.groupby(day.to_numpy()).last()
    prevc = daily.shift(1)
    avg50 = daily.pct_change().abs().rolling(50).mean().shift(1)
    pc = prevc.reindex(day.to_numpy()).set_axis(closes.index)
    a50 = avg50.reindex(day.to_numpy()).set_axis(closes.index)
    move = closes / pc - 1
    strength = move / a50
    ups, dns = (move > 0).sum(axis=1), (move < 0).sum(axis=1)
    rank_up = strength.where(move > 0).rank(axis=1, ascending=False)
    rank_dn = (-strength).where(move < 0).rank(axis=1, ascending=False)
    rows = []
    for s, df in data.items():
        pos = closes.index.get_indexer(df.index)

        def side_ok(i, side, s=s, pos=pos):
            p = pos[i]
            if p < 0:
                return False
            if side == "CE":
                return ups.iloc[p] > dns.iloc[p] and rank_up[s].iloc[p] <= MOST_STRONG
            return dns.iloc[p] > ups.iloc[p] and rank_dn[s].iloc[p] <= MOST_STRONG
        for tr in trades_for(df, VARIANTS[vname], side_ok):
            g = (tr["exit"] / tr["entry"] - 1) * (1 if tr["side"] == "CE" else -1) * 100
            rows.append({"when": tr["when"], "net": g - COST_PCT, "gross": g, "how": tr["how"], "sym": s})
    return rows


def _stock_job(vname):
    return vname, stocks(vname)


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, v) for k in INDICES for v in VARIANTS]
    tools = [(k, w) for k in INDICES for w in ("rules", "bc")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_index_job, jobs, chunksize=1))
        base = dict(pool.map(_tool_job, tools, chunksize=1))
        st = dict(pool.map(_stock_job, list(VARIANTS), chunksize=1))
    W = 160
    print("=" * W)
    print(" THE ORDER-BLOCK.COM METHOD ON THE INDICES - at-the-money options, per lot after costs, ₹  (trades, won, total, PF, worst drop, worst day)")
    print("=" * W)
    print(f" {'':46s} {'TO 15 AUG 2025':66s}  | HELD-OUT")
    s_ = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    for k in INDICES:
        rules, bc = s_(base[(k, "rules")]), s_(base[(k, "bc")])
        both = slot(rules, bc)
        print(f" {k}")
        print(sds.line("   the tool's rules", rules))
        print(sds.line("   the tool's rules + big candle (live now)", both))
        for v in VARIANTS:
            rows = s_(got[(k, v)])
            print(sds.line(f"   {v}{'  <- THEIR METHOD' if v == 'AS THEIR GUIDES' else ''}", rows) if rows else f"   {v:44s} no trades")
        p = s_(got[(k, "AS THEIR GUIDES")])
        if p:
            print("   " + sds.verdict("their method vs the tool's rules + big candle", p, both).strip().replace("\n", "\n   "))
            how = pd.Series([r["how"] for r in p]).value_counts().to_dict()
            print(f"   their method's exits: {how}")
    print("\n" + "=" * W)
    print(" THE SAME METHOD ON THE 56 MARKET-MAP F&O STOCKS (Sep 2023 - Oct 2026), the move in % per trade after "
          f"{COST_PCT}% costs (no options)")
    print("=" * W)
    for v in VARIANTS:
        rows = s_(st[v])
        for lab, sel in (("to 15 Aug 2025", [r for r in rows if pd.Timestamp(r["when"]) < pd.Timestamp("2025-08-15", tz="Asia/Kolkata")]),
                         ("held-out", [r for r in rows if pd.Timestamp(r["when"]) >= pd.Timestamp("2025-08-15", tz="Asia/Kolkata")])):
            if not sel:
                print(f"   {v:20s} {lab:16s} no trades")
                continue
            net = np.array([r["net"] for r in sel]); gross = np.array([r["gross"] for r in sel])
            pf = net[net > 0].sum() / max(1e-9, -net[net < 0].sum())
            print(f"   {v:20s} {lab:16s} {len(sel):>5} trades  won {np.mean(net > 0):>4.0%}  avg {net.mean():+.3f}% a trade "
                  f"(before costs {gross.mean():+.3f}%)  total {net.sum():+8.1f}%  PF {pf:.2f}")
        if v == "AS THEIR GUIDES" and rows:
            print(f"   {'':20s} exits: {pd.Series([r['how'] for r in rows]).value_counts().to_dict()}")


if __name__ == "__main__":
    main()
