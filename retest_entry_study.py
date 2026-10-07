#!/usr/bin/env python3
"""
retest_entry_study.py — enter on a RETEST of the signal level instead of at the signal (3 years)
================================================================================
The user, 7 Oct 2026, after pasting a breakout-trading guide ("yes run the test"). Its one idea not already measured
here: "Instead of buying the immediate breakout, wait for the price to pull back slightly to the level it just broke ...
If the price drops back to that old line, holds its ground, and begins moving back up, your entry becomes significantly
safer" - and its own warning: "If the stock is extremely strong, it might rocket upwards and never give you a pullback."

THE TEST. The tool's own signals, exactly as live (the live entry checks; the signal at a 15-minute close). Today the
ticket enters there. Retest: watch the 5-minute candles that follow, for W minutes (30 / 60), for the index to come
back to the LEVEL (the signal's own price, or 0.25 ATR further back), and then for a 5-minute candle to CLOSE back on
the trade's side of it ("holds its ground") - enter at that close. No trade when the stop is touched first or the
pullback never comes. The ticket's stop and targets stay where the signal set them (a retest entry is nearer the stop:
less risk, more reward). Exits as live (T1 -> stop to T1, Supertrend trail, out at T2, the 2-hour breakeven priced as
it fills, day end); the 5-minute candles left in the entry's own 15-minute candle are walked for the stop and target
first. One position per index, the cooldown waiver, per lot after costs, real expiries; the option struck at the money
when it is bought. "Enter at the signal" must reproduce today's figures (checked).

NOT TESTED - the guide's volume rule ("2x to 3x higher than the average volume"): an index has no volume (every 5- and
15-minute row is 0), Zerodha's API cannot reach expired futures, and NSE's archives give futures volume per DAY only -
known after the close, so using it for an intraday entry would be looking ahead.

    python3 retest_entry_study.py
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt
import config
import entry_timing_study as ets
import opening_window_study as ows
import pro_study as ps
import regime_study as rs
import stop_day_study as sds

INDICES = sds.INDICES
VARIANTS = [(0.0, 30), (0.0, 60), (0.25, 30), (0.25, 60)]          # (depth in ATR beyond the signal price, window min)


def price_at(key, A, when, spot, legs, sigma, df):
    """ps.price's money for an entry at an arbitrary moment and index level (a retest fill), struck at the money then."""
    meta = config.INSTRUMENTS[key]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    exp = ps.expiry_on_or_after(key, when.date(), A["days"])
    call = legs["side"] == "CE"
    K = round(spot / step) * step
    p0 = rs.bs(spot, K, ps.years_to(exp, when), sigma, call)
    if p0 <= 0.5:
        return None
    buy = p0 * (1 + ps.SLIP) * qty
    sells, orders = 0.0, 1
    for px, j, w in legs["legs"]:
        t_exit = df.index[j] + pd.Timedelta(minutes=15)
        p1 = rs.bs(px, K, ps.years_to(exp, max(t_exit, when)), sigma, call)
        sells += max(p1 * (1 - ps.SLIP), 0.0) * qty * w
        orders += 1
    brokerage = rs.BROKERAGE_PER_ORDER * orders
    stt = rs.STT_SELL * sells
    txn = rs.TXN[exch] * (buy + sells)
    sebi = rs.SEBI_PER_RUPEE * (buy + sells)
    stamp = rs.STAMP_BUY * buy
    gst = rs.GST * (brokerage + txn + sebi)
    return sells - buy - (brokerage + stt + txn + sebi + stamp + gst)


def retest(tr, i, df, df5, atr, depth, window):
    """(entry time, entry index level, 15-minute bar the entry falls in, the 5-minute candles left in that bar) or None."""
    ce = tr["side"] == "CE"
    t0 = df.index[i] + pd.Timedelta(minutes=15)
    level = tr["entry"] - (1 if ce else -1) * depth * atr
    a = df5.index.searchsorted(t0)
    end = t0 + pd.Timedelta(minutes=window)
    touched = False
    for n in range(a, len(df5)):
        t = df5.index[n]
        if t >= end or t.date() != t0.date():
            return None
        hi, lo, cl = df5["High"].iat[n], df5["Low"].iat[n], df5["Close"].iat[n]
        if (lo <= tr["stop"]) if ce else (hi >= tr["stop"]):
            return None                                   # invalidated before it held
        if (lo <= level) if ce else (hi >= level):
            touched = True
        if touched and ((cl > level) if ce else (cl < level)):
            t_e = t + pd.Timedelta(minutes=5)
            bar_start = (t_e - pd.Timedelta(seconds=1)).floor("15min")
            e = df.index.searchsorted(bar_start)
            if e >= len(df) or df.index[e] != bar_start:
                return None
            rest = df5.iloc[n + 1:df5.index.searchsorted(bar_start + pd.Timedelta(minutes=15))]
            return t_e, float(cl), e, rest
    return None


def _job(k):
    c = sds._context()
    df, pre, st, A = c["hists"][k], c["pre"][k], c["st"][k], c["A"][k]
    df5 = ets.load_5m(k)
    sig = c["F"][k]["sigma"].to_numpy()
    atr = pre["atr"].to_numpy()
    adx = pre["adx"]
    target = c["target"]
    trades = bt.run(k, df, gate=c["live"][k])["trades"]
    pos = {t: n for n, t in enumerate(df.index)}
    out = {"base": [], **{v: [] for v in VARIANTS}}
    stats = {v: {"signals": 0, "filled": 0, "pairs": [], "unfilled": []} for v in VARIANTS}
    for tr in trades:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        # today: in at the signal
        legs = ows.simulate_live(A, i, tr, st, target)
        p = ps.price(k, df, A, i, tr, legs, s)
        if p is None:
            continue
        exit_px, exit_bar, _ = legs[-1]
        via = "stop" if abs(exit_px - tr["stop"]) < 1e-6 else ("target" if abs(exit_px - tr[target]) < 1e-6 else "other")
        base = dict(p, side=tr["side"], closed_via=via, exit_adx=float(adx.iloc[exit_bar]), index=k, sig=tr["when"])
        out["base"].append(base)
        # the self-check of the retest pricing: an entry AT the signal prices exactly as ps.price
        chk = price_at(k, A, df.index[i] + pd.Timedelta(minutes=15), tr["entry"], {"side": tr["side"], "legs": legs}, s, df)
        assert chk is not None and abs(chk - p["net"]) < 1e-6, (k, tr["when"], chk, p["net"])
        for v in VARIANTS:
            stats[v]["signals"] += 1
            got = retest(tr, i, df, df5, atr[i], *v)
            if got is None:
                stats[v]["unfilled"].append(p["net"])
                continue
            t_e, px, e, rest = got
            ce = tr["side"] == "CE"
            t2 = dict(tr, entry=px)
            legs2 = None
            for _, r in rest.iterrows():                      # the rest of the entry's own 15-minute candle
                if (r["Low"] <= tr["stop"]) if ce else (r["High"] >= tr["stop"]):
                    legs2 = [(tr["stop"], e, 1.0)]
                    break
                if (r["High"] >= tr[target]) if ce else (r["Low"] <= tr[target]):
                    legs2 = [(tr[target], e, 1.0)]
                    break
            if legs2 is None:
                legs2 = ows.simulate_live(A, e, t2, st, target)
            net = price_at(k, A, t_e, px, {"side": tr["side"], "legs": legs2}, s, df)
            if net is None:
                continue
            stats[v]["filled"] += 1
            stats[v]["pairs"].append((p["net"], net, px - tr["entry"] if ce else tr["entry"] - px))
            xp, xb, _ = legs2[-1]
            via2 = "stop" if abs(xp - tr["stop"]) < 1e-6 else ("target" if abs(xp - tr[target]) < 1e-6 else "other")
            out[v].append({"when": t_e, "exit_time": df.index[xb] + pd.Timedelta(minutes=15), "net": net, "side": tr["side"],
                           "closed_via": via2, "exit_adx": float(adx.iloc[xb]), "index": k, "sig": tr["when"]})
    return k, out, stats


def main():
    from concurrent.futures import ProcessPoolExecutor
    print("Replaying the live signals on all three indices, side by side...", flush=True)
    with ProcessPoolExecutor(max_workers=len(INDICES)) as ex:
        got = list(ex.map(_job, INDICES))
    rows = {key: [r for _, out, _ in got for r in out[key]] for key in ["base"] + VARIANTS}
    stats = {v: {"signals": sum(s[v]["signals"] for _, _, s in got), "filled": sum(s[v]["filled"] for _, _, s in got),
                 "pairs": [x for _, _, s in got for x in s[v]["pairs"]], "unfilled": [x for _, _, s in got for x in s[v]["unfilled"]]}
             for v in VARIANTS}
    base = sds.per_index(rows["base"])
    b = sds.split(base)
    assert round(b["is"]["total"]) == 437509 and round(b["oos"]["total"]) == 125227, \
        f"entering at the signal must be today: {b['is']['total']:.0f} / {b['oos']['total']:.0f}"

    print("=" * 172)
    print(" NIFTY + Bank Nifty + Sensex, per lot, after costs, real expiries.  columns: trades, won, total, profit factor, "
          "worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print("-" * 172)
    print(sds.line("enter at the signal - TODAY", base))
    res = {}
    for v in VARIANTS:
        depth, window = v
        res[v] = sds.per_index(rows[v])
        name = f"retest {'the signal price' if not depth else f'{depth:g} ATR beyond it'}, wait {window} min"
        print(sds.line(name, res[v]))
    print("\n VERDICT - KEEP only if more profit than today in BOTH periods (worst drop / worst day: minus = better)")
    for v in VARIANTS:
        depth, window = v
        print(sds.verdict(f"retest {'signal price' if not depth else f'{depth:g} ATR'}, {window} min", res[v], base))

    print("\n HOW OFTEN THE PULLBACK CAME - and what the trades it never came for made under today's entry")
    by_sig = {(r["index"], r["sig"]): r for r in base}
    for v in VARIANTS:
        depth, window = v
        filled = {(r["index"], r["sig"]) for r in rows[v]}
        missed = [r for key, r in by_sig.items() if key not in filled]
        kept = [r for key, r in by_sig.items() if key in filled]
        sm, sk = sds.split(missed), sds.split(kept)
        print(f"   {'signal price' if not depth else f'{depth:g} ATR':13s} {window:>2} min: pulled back on "
              f"{100 * stats[v]['filled'] / max(stats[v]['signals'], 1):4.1f}% of signals.  Today's trades it would MISS: "
              f"{sm['is']['n'] + sm['oos']['n']:>4} made ₹{sm['is']['total']:>+9,.0f} / ₹{sm['oos']['total']:>+9,.0f};"
              f"  the ones it takes made ₹{sk['is']['total']:>+9,.0f} / ₹{sk['oos']['total']:>+9,.0f} at today's entry")
    print("\n LIKE FOR LIKE - every signal on its own (no one-position walk): the SAME trade entered at the signal vs at the retest")
    for v in VARIANTS:
        depth, window = v
        pr = stats[v]["pairs"]
        a = np.mean([x[0] for x in pr]); r = np.mean([x[1] for x in pr]); worse = np.mean([x[2] for x in pr])
        u = np.mean(stats[v]["unfilled"]) if stats[v]["unfilled"] else 0
        print(f"   {'signal price' if not depth else f'{depth:g} ATR':13s} {window:>2} min: where it pulled back ({len(pr)}): at the signal "
              f"₹{a:>+5,.0f}/trade, at the retest ₹{r:>+5,.0f}/trade (bought {worse:+.1f} index points from the signal price); "
              f"where it never did ({len(stats[v]['unfilled'])}): ₹{u:>+5,.0f}/trade at the signal")
    print("\n NOT TESTED: the guide's 2-3x volume rule - no intraday volume exists for the indices (all 0), and NSE's "
          "futures volume is per DAY (known only after the close).")


if __name__ == "__main__":
    main()
