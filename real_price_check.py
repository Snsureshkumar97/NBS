#!/usr/bin/env python3
"""
real_price_check.py — the systems' trades over the weeks the recorder has, priced with REAL option prices
================================================================================
The user, 28 Oct 2026: "with the new rules how are the results for these past two months which we have real data".
The setup since 28 Oct: the TradingView rating on all four indices (with the big candle on Nifty, Bank Nifty and
Sensex), the rules' stop steps back. record_options.py has kept every traded index option's minute prices since
10 Sep 2026 (the nearest two expiries; every trading day to 26 Oct recorded).

Each system's backtest trades in that window (honest_retest.py's harness: honestly filled stops, one ticket at a
time per index) - but each priced with the recorded option itself: bought at the entry minute, sold at the minute
the index reached the exit level (a stop or target: the first minute of the exit candle that touched it; a close:
the candle's last minute), after every charge and 0.25% slippage a side (regime_study.net_rupees), per lot:
  NEAREST   the at-the-money option of the nearest expiry - what the tool buys
  NEXT      the same strike on the next recorded expiry - next month on Bank Nifty and Midcap (20+ days out), only
            next week on Nifty and Sensex (the recorder kept nothing further out before 29 Oct)
A trade on a day with no recording, or a contract with no price at its minutes, is left out and counted.

RESULT (28 Oct 2026; real_price_check_results.txt). Per lot, after costs, 10 Sep - 26 Oct, the four indices pooled,
NEAREST expiry: the TradingView rating -49,795 (165 trades priced, 34% won), with the big candle -48,190; the rules
-4,723 (132, 48%); the big candle +14,255 (32, 44%). By index, TradingView + big candle: Nifty -10,880, Bank Nifty
-33,838, Sensex +5,693, Midcap -9,165 (22 of 50 priced - Midcap's at-the-money options often have no minute candle).
NEXT recorded expiry on the same trades: TradingView + big candle -18,133 against -40,094 - next month on Bank Nifty
(-21,721 against -34,832) and Midcap (+12,636 against -3,205) helps; next week on Nifty and Sensex does not. Six and a
half weeks: about one standard error for the rating - but the same way as the corrected six-year backtest.

    python3 real_price_check.py OPTION_HISTORY_DIR      (HOME at the six-year history, candles to 26 Oct 2026)
    python3 real_price_check.py daily OPTION_HISTORY_DIR    (the same, day by day)
    python3 real_price_check.py daily_levels OPTION_HISTORY_DIR    (day by day, with the levels check)
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
IST = "Asia/Kolkata"
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
SINCE = pd.Timestamp("2026-09-10", tz=IST)
UNTIL = pd.Timestamp("2026-10-27", tz=IST)


def _job(job):
    """(key, system) -> rows with when, exit_time, side, entry_spot, exit_spot, how ("level" or "close")."""
    key, system = job
    if system == "TV":
        import honest_retest as h
        rows = h._job((key, "tv", None))[1]["TV_RATING"]
        return job, [dict(r, how="level" if r["closed_via"] in ("stop", "target") else "close") for r in rows]
    if system == "RULES":
        import order_block_study as ob
        import t3_exit_recheck_study as t3
        real, seen = t3.sim, {}

        def sim(A, i, tr, st, target, **kw):
            legs, how, t1 = real(A, i, tr, st, target, **kw)
            seen[i] = (tr["entry"], legs[-1][0], "level" if how in ("stop", "target") else "close")
            return legs, how, t1
        t3.sim = sim
        make = lambda df, pre: (None, None, lambda i, p: p.update(entry_spot=seen[i][0], exit_spot=seen[i][1],
                                                                  how=seen[i][2]))
        return job, ob.rules_rows(key, make)
    import big_candle_study as bcs
    import indian_strategies_study as iss
    import one_target_study as ots
    real_walk, real_net, seen = iss.walk, iss.opt_net, {}

    def walk(bars, start, side, stop, target, custom_exit=None, last="15:15"):
        px, j = real_walk(bars, start, side, stop, target, custom_exit, last)
        seen["how"] = "close" if px == float(bars["Close"].iloc[j]) else "level"
        return px, j

    def opt_net(k, days, t_in, t_out, s_in, s_out, side, sigma):
        seen[t_in] = (s_in, s_out, seen.get("how", "level"))
        return real_net(k, days, t_in, t_out, s_in, s_out, side, sigma)
    iss.walk, iss.opt_net = walk, opt_net
    rows = bcs.run(key, ots.BC)
    out = []
    for r in rows:
        s_in, s_out, how = seen[r["when"]]
        out.append(dict(r, exit_time=r["out"], entry_spot=s_in, exit_spot=s_out, how=how))
    return job, out


def real_net(r, P, which):
    """The trade on the recorded option - which: 0 the nearest expiry, 1 the next recorded one. -> (net per lot, days
    to that expiry at entry) or None."""
    import config
    import live_vs_backtest as lv
    import regime_study as rs
    k = r["index"]
    meta = config.INSTRUMENTS[k]
    t_in, t_out = pd.Timestamp(r["when"]), pd.Timestamp(r["exit_time"])
    d = t_in.date().isoformat()
    exps = [e for e in P.expiries(k, d) if e >= d]
    if len(exps) <= which:
        return None
    exp = exps[which]
    K = round(r["entry_spot"] / meta["strike_step"]) * meta["strike_step"]
    opt = P.option(k, d, exp, K, r["side"])
    p0 = lv.at_open(opt, t_in)
    if p0 is None or p0 <= 0.5:
        return None
    exit_t = t_out - pd.Timedelta(minutes=1)
    if r["how"] == "level":
        idx = P.index(k, d)
        if idx is not None and len(idx):
            win = idx[(idx.index >= t_out - pd.Timedelta(minutes=15)) & (idx.index < t_out)]
            px = r["exit_spot"]
            hit = win[(win["low"] <= px) & (win["high"] >= px)]
            if len(hit):
                exit_t = hit.index[0]
            elif len(win):
                exit_t = win.index[0]                  # opened past the level: out at the candle's first minute
    p1 = lv.at_close(opt, exit_t)
    if p1 is None:
        return None
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    days_left = (pd.Timestamp(exp, tz=IST) + pd.Timedelta(hours=15, minutes=30) - t_in).total_seconds() / 86400
    return rs.net_rupees(p0, p1, meta["lot_size"], exch, 0.0025), days_left


def main():
    import multiprocessing as mp
    import live_vs_backtest as lv
    import order_block_site_study as obs
    P = lv.Prices(sys.argv[1])
    jobs = [(k, s) for k in KEYS for s in ("TV", "RULES", "BC")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    sys_rows = {}
    for k in KEYS:
        tv, ru, bc = (s([r for r in got[(k, x)] if SINCE <= pd.Timestamp(r["when"]) < UNTIL]) for x in ("TV", "RULES", "BC"))
        for r in tv + ru + bc:
            r["index"] = k
        sys_rows[(k, "TradingView rating")] = tv
        sys_rows[(k, "the rules")] = ru
        sys_rows[(k, "the big candle")] = bc
        sys_rows[(k, "TradingView + big candle")] = s(obs.slot(tv, bc)) if k != "MIDCPNIFTY" else tv
    print(f"REAL RECORDED OPTION PRICES, {SINCE.date()} to {(UNTIL - pd.Timedelta(days=1)).date()} - per lot, after "
          f"charges and 0.25% slippage a side. Your account since 28 Oct: TradingView + big candle (Midcap: no big candle).")
    print(f"{'':11s} {'system':26s} {'trades':>6s} {'priced':>6s} | {'NEAREST':>9s} {'won':>4s} | {'NEXT':>9s} {'won':>4s}"
          f" {'(days out)':>10s}")
    tot = {}
    for k in KEYS + ("ALL FOUR",):
        for name in ("TradingView + big candle", "TradingView rating", "the big candle", "the rules"):
            rows = (sys_rows[(k, name)] if k != "ALL FOUR" else [r for kk in KEYS for r in sys_rows[(kk, name)]])
            near = [real_net(r, P, 0) for r in rows]
            nxt = [real_net(r, P, 1) for r in rows]
            both = [(a[0], b[0], b[1]) for a, b in zip(near, nxt) if a and b]
            n0 = [a[0] for a in near if a]
            if not rows:
                continue
            dmed = pd.Series([b[2] for b in both]).median() if both else float("nan")
            print(f"{k:11s} {name:26s} {len(rows):6d} {len(n0):6d} | {sum(n0):+9,.0f} {100 * sum(x > 0 for x in n0) / max(len(n0), 1):3.0f}% |"
                  f" {sum(b[1] for b in both):+9,.0f} {100 * sum(b[1] > 0 for b in both) / max(len(both), 1):3.0f}%"
                  f" {dmed:8.0f} d   (nearest on the same {len(both)}: {sum(b[0] for b in both):+,.0f})")
        print()


def daily():
    """The same trades, day by day (the user, 28 Oct 2026: "give me by each day")."""
    import multiprocessing as mp
    import live_vs_backtest as lv
    import order_block_site_study as obs
    P = lv.Prices(sys.argv[2])
    jobs = [(k, s_) for k in KEYS for s_ in ("TV", "RULES", "BC")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    per = []                                         # one row a priced trade
    for k in KEYS:
        tv, ru, bc = (s([dict(r, index=k) for r in got[(k, x)] if SINCE <= pd.Timestamp(r["when"]) < UNTIL])
                      for x in ("TV", "RULES", "BC"))
        setup = s(obs.slot(tv, bc)) if k != "MIDCPNIFTY" else tv
        for name, rows in (("SETUP", setup), ("TV", tv), ("RULES", ru), ("BC", bc)):
            for r in rows:
                a, b = real_net(r, P, 0), (real_net(r, P, 1) if name == "SETUP" else None)
                per.append({"day": pd.Timestamp(r["when"]).date(), "index": k, "system": name,
                            "near": a[0] if a else None, "next": b[0] if b else None})
    d = pd.DataFrame(per)
    days = sorted(set(d["day"]))
    recorded = {pd.Timestamp(f[:10]).date() for f in os.listdir(sys.argv[2]) if f.endswith(".csv.gz")}
    f = lambda v: "      -" if v != v or v is None else f"{v:+8,.0f}"
    print("REAL RECORDED OPTION PRICES, DAY BY DAY - per lot, after charges and 0.25% slippage a side, nearest expiry")
    print("SETUP = your account since 28 Oct: the TradingView rating + the big candle (Midcap: the rating alone)")
    print(f"{'day':12s} {'SETUP':>8s} {'trades':>6s} {'running':>9s} | {'TV alone':>8s} {'rules':>8s} {'big cdl':>8s} |"
          f" {'SETUP on next expiry':>20s}")
    run = 0.0
    for day in days:
        if day not in recorded:
            print(f"{day.strftime('%a %d %b'):12s}   not recorded (the recorder was down)")
            continue
        x = d[d["day"] == day]
        tot = lambda sysname, col="near": x[(x["system"] == sysname)][col].dropna().sum() \
            if len(x[(x["system"] == sysname)][col].dropna()) else float("nan")
        su = x[x["system"] == "SETUP"]
        sv = su["near"].dropna().sum()
        run += sv
        print(f"{day.strftime('%a %d %b'):12s} {f(sv)} {len(su['near'].dropna()):6d} {run:+9,.0f} | {f(tot('TV'))} "
              f"{f(tot('RULES'))} {f(tot('BC'))} | {f(tot('SETUP', 'next')):>20s}")
    print()
    print("SETUP, BY INDEX AND DAY (per lot, nearest expiry; '-' = no trade or no recorded price)")
    print(f"{'day':12s} " + " ".join(f"{k:>10s}" for k in KEYS) + f" {'all four':>10s}")
    for day in days:
        if day not in recorded:
            continue
        x = d[(d["day"] == day) & (d["system"] == "SETUP")]
        cells = []
        for k in KEYS:
            v = x[x["index"] == k]["near"].dropna()
            cells.append(f"{v.sum():+10,.0f}" if len(v) else f"{'-':>10s}")
        print(f"{day.strftime('%a %d %b'):12s} " + " ".join(cells) + f" {x['near'].dropna().sum():+10,.0f}")
    su = d[d["system"] == "SETUP"].groupby("day")["near"].sum()
    su = su[[day in recorded for day in su.index]]
    print()
    print(f"SETUP: {len(su)} recorded days - {int((su > 0).sum())} up, {int((su < 0).sum())} down, "
          f"{int((su == 0).sum())} flat; best {su.max():+,.0f}, worst {su.min():+,.0f}, total {su.sum():+,.0f}")


def _tv_levels(key):
    """The TradingView rating with the levels check (sr_study LEVELS_1.5, levels.py), rows as _job's."""
    import sr_study as sr
    rows = sr._job((key, "TV", "LEVELS_1.5"))[1]
    return key, [dict(r, how="level" if r["closed_via"] in ("stop", "target") else "close") for r in rows]


def daily_levels():
    """Day by day with the levels check on (the user, 28 Oct 2026: "give me day by day with these rules")."""
    import multiprocessing as mp
    import live_vs_backtest as lv
    import order_block_site_study as obs
    P = lv.Prices(sys.argv[2])
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        tvl = dict(pool.map(_tv_levels, KEYS))
        got = dict(pool.map(_job, [(k, s_) for k in KEYS for s_ in ("TV", "BC")], chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    win = lambda rows, k: s([dict(r, index=k) for r in rows if SINCE <= pd.Timestamp(r["when"]) < UNTIL])
    per = []
    for k in KEYS:
        tv, tl, bc = win(got[(k, "TV")], k), win(tvl[k], k), win(got[(k, "BC")], k)
        new = s(obs.slot(tl, bc)) if k != "MIDCPNIFTY" else tl
        old = s(obs.slot(tv, bc)) if k != "MIDCPNIFTY" else tv
        for name, rows in (("NEW", new), ("OLD", old)):
            for r in rows:
                a = real_net(r, P, 0)
                b = real_net(r, P, 1) if name == "NEW" else None
                per.append({"day": pd.Timestamp(r["when"]).date(), "index": k, "system": name,
                            "near": a[0] if a else None, "next": b[0] if b else None})
    d = pd.DataFrame(per)
    days = sorted(set(d["day"]))
    f = lambda v: "      -" if v is None or v != v else f"{v:+8,.0f}"
    print("REAL RECORDED OPTION PRICES, DAY BY DAY - per lot, after charges and 0.25% slippage a side")
    print("NEW = the TradingView rating with yesterday's levels (1.5x room) + the big candle (Midcap: no big candle)")
    print("OLD = the same without the levels check (your account's setup until now)")
    print(f"{'day':12s} {'NEW':>8s} {'trades':>6s} {'running':>9s} | {'NEW next exp':>12s} {'running':>9s} | "
          f"{'OLD':>8s} {'trades':>6s} {'running':>9s}")
    rn = rx = ro = 0.0
    for day in days:
        x = d[d["day"] == day]
        nw, od = x[x.system == "NEW"], x[x.system == "OLD"]
        a, b, c = nw["near"].dropna().sum(), nw["next"].dropna().sum(), od["near"].dropna().sum()
        rn, rx, ro = rn + a, rx + b, ro + c
        print(f"{day.strftime('%a %d %b'):12s} {f(a)} {len(nw['near'].dropna()):6d} {rn:+9,.0f} | {f(b):>12s} "
              f"{rx:+9,.0f} | {f(c)} {len(od['near'].dropna()):6d} {ro:+9,.0f}")
    for name in ("NEW", "OLD"):
        g = d[d.system == name].groupby("day")["near"].sum()
        print(f"{name}: {len(g)} days with trades - {int((g > 0).sum())} up, {int((g < 0).sum())} down; "
              f"{int(d[d.system == name]['near'].notna().sum())} trades priced; total {g.sum():+,.0f}")
    print()
    print("NEW, BY INDEX AND DAY (nearest expiry)")
    print(f"{'day':12s} " + " ".join(f"{k:>10s}" for k in KEYS) + f" {'all four':>10s}")
    for day in days:
        x = d[(d["day"] == day) & (d["system"] == "NEW")]
        cells = [f"{x[x['index'] == k]['near'].dropna().sum():+10,.0f}" if len(x[x['index'] == k]['near'].dropna())
                 else f"{'-':>10s}" for k in KEYS]
        print(f"{day.strftime('%a %d %b'):12s} " + " ".join(cells) + f" {x['near'].dropna().sum():+10,.0f}")


if __name__ == "__main__":
    {"daily": daily, "daily_levels": daily_levels}.get(sys.argv[1] if len(sys.argv) > 1 else "", main)()
