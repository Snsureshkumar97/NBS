#!/usr/bin/env python3
"""
order_block_study.py — order blocks, market structure and fair-value gaps ("Smart Money Concepts") on every live system
================================================================================
The user, 19-20 Oct 2026, after a product page for a paid Windows app (an "order block & order flow analyzer"): "can
you go through this link and see anyfeature is usefull ... for our indian market or crypto market", then "yes run both
tests" (this one and next_day_range_study.py). The app's own order-block rules are in a paid PDF, so the textbook public
definitions are used (the same as the widely used open-source "Smart Money Concepts" indicators), with one swing length.

DEFINITIONS - FIXED BEFORE ANY RESULT WAS SEEN (20 Oct 2026). On 15-minute candles, at each candle's close, from candles
up to and including it (no look-ahead):
  SWING      a swing high is a high above the 5 candles either side of it (a swing low likewise) - so it is known only 5
             candles later
  STRUCTURE  a close above the last swing high is a bullish break (BOS, or CHoCH when it turns a bearish structure);
             a close below the last swing low a bearish one. The structure is the side of the last break.
  ORDER BLOCK on a bullish break, the candle with the lowest low between the broken swing high and the break: its high
             to its low is a demand zone (a bearish break: the highest-high candle, a supply zone). A zone lasts until a
             candle closes through it (below a demand zone's low, above a supply zone's high); the last 10 of each kept.
  FAIR-VALUE GAP  a candle whose low is above the high two candles before it (bullish), or whose high is below that
             candle's low (bearish).
Each variant adds ONE condition to a system as live runs it today (a blocked signal frees the slot, as it would live);
ATR is the tool's own ATR(14) on the 15-minute candles:
  STRUCT    a buy (CE / long) only while the structure is bullish, a sell (PE / short) only while it is bearish
  OB_PATH   no buy with a supply zone within 1 x ATR above the price (or the price inside one); no sell with a demand zone
            within 1 x ATR below
  OB_NEAR   a buy only with a demand zone within 2 x ATR below the price (or the price inside one) - "buy at demand"; a
            sell only with a supply zone within 2 x ATR above
  FVG       a buy only when a bullish fair-value gap formed in the last 3 candles (a sell: a bearish one) - displacement
  OBSTOP    (the Indian rules only - the order block as the stop, not an entry condition) the stop goes just past the
            nearest demand zone's low (a put: supply zone's high), 0.1 x ATR beyond it, when that is tighter than the
            rules' own stop and at least 0.5 x ATR away; otherwise the rules' stop. Targets unchanged.
THE SYSTEMS, each as it runs live today:
  Indian rules (Nifty, Sensex, Midcap - Nifty and Midcap/Sensex run the rules on the accounts): the room as live gives it
            since 15 Oct (ROOM_LIMITS "all" - room_fix_study's B: the expected-move cap, the ~10-day normal day), out at
            T3 with the stop to T1 / T2, the Supertrend trail after T1, the 2-hour breakeven, the T1-exit re-entry; at
            the money on the real expiry, 0.25% slippage, every charge, per lot (Sensex and Midcap asserted to
            room_fix_study's B figures)
  Bank Nifty: TradingView rating entry + the Trend Rider's exit (tv_ratings_study "tv_ours", asserted)
  Gold: TradingView MA rating, stop 3 x ATR, target 3 x ATR (1:1, live since 15 Oct - gold_tp_study's 3 x ATR row,
            asserted), Exness real ticks, spread and swap, $ per gold lot
  Bitcoin: the tool's own crypto votes (the default system), crypto_engine_votes_study's plain 1R (asserted)
VERDICT: kept only if more than today's system in BOTH the first two years (to 15 Aug 2025) and the held-out year. The
Indian rules pooled (Nifty + Sensex + Midcap), and then on each index too - five variants are tried, so one passing by
luck is possible.

RESULT (20 Oct 2026, Indian history to 11 Sep 2026, Exness ticks to Aug 2026; order_block_study_results.txt) - NOTHING
BUILT. Every BASE reproduced its system's own figure. Profit against today's system, first two years / held-out:
  Indian rules (pooled)  STRUCT -61,767 / -49,823   OB_PATH -216,769 / -118,387   OB_NEAR -267,936 / -157,151   all DROP
                         FVG -111,119 / +5,397 DROP (passes on Nifty alone: +20,233 / +7,700)
                         OBSTOP +3,959 / +335 - next to nothing: the zone is almost never inside the rules' own stop
  Bank Nifty (TradingView)  FVG +13,005 / +24,428 (the only clear pass; worst drop -29,561 / -16,806); the rest DROP
  Gold (1:1)  all four mixed (one period up, the other down) - DROP     Bitcoin  FVG -21,196 / +505 against -34,831 /
              -3,057: less bad, still losing; the rest DROP
BACKWARDS, like the RSI cap and range-exhaustion studies: the Indian trades bought right under a supply zone (what
OB_PATH blocks) earned MORE (PF 1.46 / 1.33) than the rest (1.12 / 1.15) - the rules ride breakouts, and a breakout is
by definition a move into the last supply.
THE FVG CHECK (order_block_fvg_check.py, after this result - a check, not a decision): the gap in the last 1-5 candles.
Bank Nifty's TradingView passes at 2, 3, 4 and 5 (not 1), worse in 2023-H2 and 2024-H1, better in the five half-years
after. But the same system on NIFTY loses at every window (3: -21,967 / -42,214), and Nifty's rules pass at 3 only. One
index of one system, failing on the sister index: read as not proven. Offered to the user as an off-by-default switch at
most.

    /usr/bin/python3 order_block_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SWING = 5
MAX_OB = 10
RULES = ("NIFTY", "SENSEX", "MIDCPNIFTY")
ENTRY = ("STRUCT", "OB_PATH", "OB_NEAR", "FVG")
VARIANTS = ("BASE",) + ENTRY + ("OBSTOP",)
B_FIGS = {"SENSEX": (78098, 55603), "MIDCPNIFTY": (209886, 86911)}      # room_fix_study B (live room since 15 Oct)
TV_FIGS = (181285, 76234)                                                 # tv_ratings_study tv_ours, Bank Nifty
GOLD_FIGS = (2174, 55620)                                                 # gold_tp_study 3 x ATR (config.CFD_RULES note)
BTC_FIGS = (-34831, -3057)                                                # crypto_engine_votes_study plain 1R


# ---------------------------------------------------------------- the definitions
def smc(df, atr, L=SWING):
    """Structure, order blocks and fair-value gaps at each candle's close, from candles up to and including it."""
    import numpy as np
    hi, lo, cl = (df[c].to_numpy(float) for c in ("High", "Low", "Close"))
    n = len(cl)
    trend = np.zeros(n, np.int8)
    d_bull, d_bear = np.full(n, np.inf), np.full(n, np.inf)
    bull_bot, bear_top = np.full(n, np.nan), np.full(n, np.nan)
    sh = sl = None                  # [level, index, broken]
    bulls, bears = [], []           # active zones, (top, bottom)
    t = 0
    for i in range(n):
        j = i - L
        if j - L >= 0:              # the candle L back is a swing point only once L candles have followed it
            w = hi[j - L:i + 1]
            if hi[j] == w.max() and (w == hi[j]).sum() == 1:
                sh = [hi[j], j, False]
            w = lo[j - L:i + 1]
            if lo[j] == w.min() and (w == lo[j]).sum() == 1:
                sl = [lo[j], j, False]
        c = cl[i]
        bulls = [z for z in bulls if c >= z[1]]          # a close below a demand zone ends it
        bears = [z for z in bears if c <= z[0]]          # a close above a supply zone ends it
        if sh is not None and not sh[2] and c > sh[0]:
            k = sh[1] + int(np.argmin(lo[sh[1]:i + 1]))
            bulls = (bulls + [(hi[k], lo[k])])[-MAX_OB:]
            sh[2], t = True, 1
        if sl is not None and not sl[2] and c < sl[0]:
            k = sl[1] + int(np.argmax(hi[sl[1]:i + 1]))
            bears = (bears + [(hi[k], lo[k])])[-MAX_OB:]
            sl[2], t = True, -1
        trend[i] = t
        below = [(max(0.0, c - top), bot) for top, bot in bulls if bot <= c]
        if below:
            d_bull[i], bull_bot[i] = min(below)
        above = [(max(0.0, bot - c), top) for top, bot in bears if top >= c]
        if above:
            d_bear[i], bear_top[i] = min(above)
    fb, fs = np.zeros(n, bool), np.zeros(n, bool)
    fb[2:], fs[2:] = lo[2:] > hi[:-2], hi[2:] < lo[:-2]
    recent = lambda x: x | np.r_[False, x[:-1]] | np.r_[False, False, x[:-2]]
    return {"trend": trend, "d_bull": d_bull, "d_bear": d_bear, "bull_bot": bull_bot, "bear_top": bear_top,
            "fvg_bull": recent(fb), "fvg_bear": recent(fs), "atr": np.asarray(atr, float)}


def allowed(v, S, i, buy):
    """Whether variant v lets a buy (CE / long) or a sell through at candle i. No ATR yet blocks nothing."""
    a = S["atr"][i]
    if v == "STRUCT":
        return int(S["trend"][i]) == (1 if buy else -1)
    if v == "FVG":
        return bool(S["fvg_bull"][i] if buy else S["fvg_bear"][i])
    if not (a == a and a > 0):
        return True
    if v == "OB_PATH":
        return (S["d_bear"][i] if buy else S["d_bull"][i]) >= 1.0 * a
    if v == "OB_NEAR":
        return (S["d_bull"][i] if buy else S["d_bear"][i]) <= 2.0 * a
    return True


def ob_stop(S, i, tr):
    """OBSTOP: the trade with its stop just past the nearest zone behind it, when tighter and at least 0.5 x ATR away."""
    a = S["atr"][i]
    buy, e = tr["side"] == "CE", tr["entry"]
    edge = S["bull_bot"][i] if buy else S["bear_top"][i]
    if not (a == a and a > 0) or not (edge == edge):
        return tr
    new = edge - 0.1 * a if buy else edge + 0.1 * a
    risk_new, risk_old = (e - new, e - tr["stop"]) if buy else (new - e, tr["stop"] - e)
    return dict(tr, stop=new) if 0.5 * a <= risk_new < risk_old else tr


# ---------------------------------------------------------------- the Indian rules, as live runs them
def rules_rows(key, make=None, room_hook=None):
    """The rules as live runs them today, 15-minute walk, per lot after costs (t3_exit_recheck_study's "t3re" on
    live_room_study's B room). make(df, pre) -> (gate_extra(i, buy) | None, stop_fn(i, tr) | None, annotate(i, row) |
    None); room_hook(key) wraps the room further, after B (next_day_range_study)."""
    import math
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import index_candidates_study as ics
    import indicators as ind
    import live_room_study as lrs
    import pro_study as ps
    import regime_study as rs
    import reversal_exit_study as res
    import t1_reentry_study as t1r
    import t3_exit_recheck_study as t3
    ps.SLIP = 0.0025
    if key == "MIDCPNIFTY":                          # a fresh process per job: this patch dies with it
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    lrs.install_live_room(key, chain=True, normal_days=10)
    if room_hook:
        room_hook(key)
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    F = rs.features(df, rs.load_vix(), nrv)
    A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
         "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(), "days": set(df.index.date)}
    pre = bt.precompute(df, key)
    st = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
    gate_extra, stop_fn, annotate = make(df, pre) if make else (None, None, None)
    gate = lambda i, rec: (res.live_gate(key, i, rec, df)
                           and (gate_extra is None or gate_extra(i, rec["option_type"] == "CE")))
    trades = bt.run(key, df, gate=gate)["trades"]
    pos = {t: n for n, t in enumerate(df.index)}
    sig = F["sigma"].to_numpy()
    adx = pre["adx"]
    rows = []
    for tr in trades:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        if stop_fn:
            tr = stop_fn(i, tr)
        legs, how, t1_done = t3.sim(A, i, tr, st, "t3", step_t2=True)
        p = ps.price(key, df, A, i, tr, legs, s)
        if p is None:
            continue
        px, j, _ = legs[-1]
        p["side"] = tr["side"]
        p["closed_via"] = ("stop" if abs(px - tr["stop"]) < 1e-6 else
                           "target" if tr.get("t3") is not None and abs(px - tr["t3"]) < 1e-6 else "other")
        p["exit_adx"] = float(adx.iloc[j])
        p["signal_time"] = df.index[i].time()
        p["index"] = key
        p["t1_exit"] = how == "stop" and t1_done
        p["how"] = how
        if annotate:
            annotate(i, p)
        rows.append(p)
    rows.sort(key=lambda r: r["when"])
    return t1r.sequential(rows, config.REENTRY_COOLDOWN_MIN, config.ADX_TREND_THRESHOLD, "A")


def _rules_job(job):
    key, v = job
    import numpy as np

    def make(df, pre):
        S = smc(df, np.asarray(pre["atr"], float))
        if v == "BASE":                               # mark what each variant would have said of today's trades
            return None, None, lambda i, p: p.update({f"ok_{e}": allowed(e, S, i, p["side"] == "CE") for e in ENTRY})
        if v == "OBSTOP":
            return None, lambda i, tr: ob_stop(S, i, tr), None
        return (lambda i, buy: allowed(v, S, i, buy)), None, None
    return job, rules_rows(key, make)


# ---------------------------------------------------------------- Bank Nifty: TradingView rating + the Trend Rider's exit
def _tv_job(job):
    _, v = job
    import numpy as np
    import backtest_intraday as bt
    import tv_ratings_study as tvs
    if v != "BASE":
        real = tvs.tv_ratings

        def filtered(df, real=real):
            """The overall rating, with a Strong reading the variant blocks read as neutral - so the one-position walk
            moves on exactly as it would live."""
            rt = real(df).copy()
            S = smc(df, np.asarray(bt.precompute(df, "BANKNIFTY")["atr"], float))
            r = rt["all"].to_numpy(float).copy()
            for i in range(len(r)):
                if (r[i] > tvs.STRONG and not allowed(v, S, i, True)) or (r[i] < -tvs.STRONG and not allowed(v, S, i, False)):
                    r[i] = 0.0
            rt["all"] = r
            return rt
        tvs.tv_ratings = filtered
    _, out = tvs._tv_job("BANKNIFTY")
    return job, out["tv_ours"]


# ---------------------------------------------------------------- crypto on Exness real ticks
def _gold_month(args):
    import gold_tp_study as gtp
    gtp.TPS = (3.0,)                                  # live since 15 Oct: target 3 x ATR (1:1)
    return gtp._job(args)


def _btc_month(args):
    import crypto_tv_ratings_study as cts
    return cts._tool_job(args)


def crypto_rows(sym):
    """Today's system's walked trades for one symbol (before one-position-at-a-time), with the candle each was signalled
    on, and the symbol's 15-minute candles."""
    from concurrent.futures import ProcessPoolExecutor
    import pandas as pd
    import cfd_tick_study as t
    import crypto_engine_votes_study as cev
    import crypto_tv_ratings_study as cts
    import exness_data as ed
    df = ed.load(sym)[["Open", "High", "Low", "Close", "Volume"]]
    months = ed.months_until(t.LAST)
    workers = max(1, min(6, (os.cpu_count() or 2) - 1))
    rows = []
    with ProcessPoolExecutor(workers) as ex:
        if sym == "XAUUSD":
            _, path = cts._prep(sym)
            parts = ex.map(_gold_month, [(y, m, path) for (y, m) in months])
        else:
            _, path = cev._entries(sym)
            parts = ex.map(_btc_month, [(sym, y, m, path) for (y, m) in months])
        for part in parts:
            rows.extend(part)
    cts.add_swap_and_direction(rows, sym, df)
    pos = {x: n for n, x in enumerate(df.index)}
    for r in rows:
        r["i"] = pos[r["when"] - pd.Timedelta(minutes=15)]
    return rows, df


def crypto_results(sym):
    import numpy as np
    import backtest_intraday as bt
    import config
    import crypto_strategy_study as cs
    import crypto_tv_ratings_study as cts
    rows, df = crypto_rows(sym)
    S = smc(df, np.asarray(bt.precompute(df, cts.SYMBOLS[sym][0])["atr"], float))
    seq = ((lambda rs_: cs.sequential(rs_)) if sym == "XAUUSD" else
           (lambda rs_: cs.sequential(rs_, cooldown_min=config.REENTRY_COOLDOWN_MIN, waiver_adx=config.ADX_TREND_THRESHOLD)))
    res = {"BASE": seq(sorted(rows, key=lambda r: r["when"]))}
    for v in ENTRY:
        res[v] = seq(sorted((r for r in rows if allowed(v, S, r["i"], r["side"] == "CE")), key=lambda r: r["when"]))
    for r in res["BASE"]:
        for e in ENTRY:
            r[f"ok_{e}"] = allowed(e, S, r["i"], r["side"] == "CE")
    p = cs.periods(res["BASE"])
    got = (round(cs.stats(p["is"], "net")["total"]), round(cs.stats(p["oos"], "net")["total"]))
    want = GOLD_FIGS if sym == "XAUUSD" else BTC_FIGS
    assert all(abs(a - b) <= 2 for a, b in zip(got, want)), f"{sym} BASE must be today's system {want}, got {got}"
    return res, S


# ---------------------------------------------------------------- the report
def main():
    import multiprocessing as mp
    import crypto_strategy_study as cs
    import stop_day_study as sds
    jobs = [(k, v) for k in RULES for v in VARIANTS]
    tv_jobs = [("BANKNIFTY", v) for v in ("BASE",) + ENTRY]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_rules_job, jobs, chunksize=1))
        got.update(dict(pool.map(_tv_job, tv_jobs, chunksize=1)))
    for k, figs in B_FIGS.items():
        s = sds.split(got[(k, "BASE")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} BASE must be room_fix_study's B {figs}"
    s = sds.split(got[("BANKNIFTY", "BASE")])
    assert (round(s["is"]["total"]), round(s["oos"]["total"])) == TV_FIGS, f"Bank Nifty BASE must be tv_ours {TV_FIGS}"
    crypto = {sym: crypto_results(sym) for sym in ("XAUUSD", "BTCUSD")}

    W = 172
    pool_ = lambda v: sorted([r for k in RULES for r in got[(k, v)]], key=lambda r: r["when"])
    print("=" * W)
    print(" ORDER BLOCKS / STRUCTURE / FAIR-VALUE GAPS on every live system. Indian: per lot after costs, ₹. columns: trades,"
          " won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" INDIAN RULES: NIFTY + SENSEX + MIDCAP (the verdict)")
    for v in VARIANTS:
        print(sds.line("   " + v, pool_(v)))
    for k in RULES:
        print(f" {k}")
        for v in VARIANTS:
            print(sds.line("   " + v, got[(k, v)]))
    print(" BANK NIFTY: TradingView rating + the Trend Rider's exit")
    for v in ("BASE",) + ENTRY:
        print(sds.line("   " + v, got[("BANKNIFTY", v)]))

    print("\n WHAT EACH CONDITION WOULD HAVE SAID OF TODAY'S INDIAN RULES TRADES (BASE), and what each group earned")
    base = pool_("BASE")
    for e in ENTRY:
        yes = [r for r in base if r.get(f"ok_{e}")]
        no = [r for r in base if not r.get(f"ok_{e}")]
        print(f"   {e}: lets through {len(yes)} of {len(base)}")
        print(sds.line("      let through", yes))
        print(sds.line("      blocked", no))

    def cline(name, rs_):
        p = cs.periods(rs_)
        f = lambda x: (lambda s: f"{s['n']:>5} {s['win']:>4.0f}% ${s['total']:>+10,.0f} PF {s['pf']:5.2f} DD ${s['dd']:>9,.0f}")(
            cs.stats(x, "net"))
        return f" {name:46s} {f(p['is'])}  | {f(p['oos'])}"
    for sym, label in (("XAUUSD", "GOLD: TradingView MA rating, 1:1 (live) - $ per gold lot, Exness ticks"),
                       ("BTCUSD", "BITCOIN: the tool's crypto votes (default) - $ per BTC, Exness ticks")):
        res, _ = crypto[sym]
        print(f" {label}")
        for v in ("BASE",) + ENTRY:
            print(cline("   " + v, res[v]))
        for e in ENTRY:
            yes = [r for r in res["BASE"] if r.get(f"ok_{e}")]
            no = [r for r in res["BASE"] if not r.get(f"ok_{e}")]
            print(cline(f"     (BASE trades {e} lets through: {len(yes)})", yes))
            print(cline(f"     (BASE trades {e} blocks: {len(no)})", no))

    print("\n VERDICT - kept only if more than today's system in BOTH periods")
    for v in VARIANTS[1:]:
        print("  " + sds.verdict(f"{v} Indian rules pooled", pool_(v), pool_("BASE")).strip())
        for k in RULES:
            print("      " + sds.verdict(f"{v} {k}", got[(k, v)], got[(k, "BASE")]).strip())
    for v in ENTRY:
        print("  " + sds.verdict(f"{v} Bank Nifty (TradingView)", got[("BANKNIFTY", v)], got[("BANKNIFTY", "BASE")]).strip())
    for sym in ("XAUUSD", "BTCUSD"):
        res, _ = crypto[sym]
        b = cs.periods(res["BASE"])
        for v in ENTRY:
            p = cs.periods(res[v])
            di = cs.stats(p["is"], "net")["total"] - cs.stats(b["is"], "net")["total"]
            do = cs.stats(p["oos"], "net")["total"] - cs.stats(b["oos"], "net")["total"]
            print(f"   {'KEEP' if di > 0 and do > 0 else 'drop'}  {v} {sym:38s} profit: in-sample {di:>+9,.0f}  held-out {do:>+9,.0f}")
    return got, crypto


if __name__ == "__main__":
    main()
