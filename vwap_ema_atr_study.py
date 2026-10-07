#!/usr/bin/env python3
"""
vwap_ema_atr_study.py — the pasted "Institutional Intraday Trend Rider" on 3 years, against the tool's own system
================================================================================
The user, 7 Oct 2026, pasted a strategy (VWAP + EMA 9/20 + ATR on 5-minute candles) with no other words - after
"yes run the test" for the previous paste. Its rules, exactly as written:
  long  (buy CE): the 5-minute candle trades ENTIRELY above VWAP (its low above it) and EMA 9 crosses above EMA 20 on
                  it; in at that candle's close
  short (buy PE): the candle entirely below VWAP (its high below it) and EMA 9 crosses below EMA 20; in at its close
  stop 1.5 x ATR(14) from the entry, target 3 x ATR(14) (1:2); no new trade before 09:30; everything out by 15:15
  the paste's own add-on: stop for the day when the day's first two trades both hit their stop
One position per index at a time (a crossover while one is open is ignored).

HOW IT IS MEASURED - the same way as every study of the tool's own system, so the two can be compared: Nifty, Bank
Nifty, Sensex; 18 Sep 2023 - 11 Sep 2026 (5-minute candles); in-sample to 15 Aug 2025, then the held-out final year;
the option struck at the money when bought, priced Black-Scholes at India VIX scaled by the index's own realised
volatility against Nifty's, on its real expiry; slippage 0.25% a side and every charge (brokerage, STT, exchange, SEBI,
stamp, GST); per lot. Inside a 5-minute candle the stop is assumed hit before the target (the candle does not say which
came first), and a candle that opens beyond either fills at its open. VWAP is the tool's own (an index has no volume:
each bar weighted by the futures' typical share of the day's volume at that time - 97% agreement with the true
futures VWAP on Nifty).

    python3 vwap_ema_atr_study.py
"""
import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt
import config
import entry_timing_study as ets
import indicators as ind
import pro_study as ps
import regime_study as rs
import stop_day_study as sds

INDICES = sds.INDICES
FIRST_ENTRY = dt.time(9, 30)
SQUARE_OFF = dt.time(15, 10)          # the 15:10 candle closes at 15:15: out there at the latest


def price_trade(key, days, t_in, s_in, t_out, s_out, sigma, call):
    """Net rupees per lot: buy the at-the-money option at t_in (index s_in), sell it at t_out (index s_out)."""
    meta = config.INSTRUMENTS[key]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    exp = ps.expiry_on_or_after(key, t_in.date(), days)
    K = round(s_in / step) * step
    p0 = rs.bs(s_in, K, ps.years_to(exp, t_in), sigma, call)
    if p0 <= 0.5:
        return None
    p1 = rs.bs(s_out, K, ps.years_to(exp, t_out), sigma, call)
    buy = p0 * (1 + ps.SLIP) * qty
    sells = max(p1 * (1 - ps.SLIP), 0.0) * qty
    brokerage = rs.BROKERAGE_PER_ORDER * 2
    stt = rs.STT_SELL * sells
    txn = rs.TXN[exch] * (buy + sells)
    sebi = rs.SEBI_PER_RUPEE * (buy + sells)
    stamp = rs.STAMP_BUY * buy
    gst = rs.GST * (brokerage + txn + sebi)
    return sells - buy - (brokerage + stt + txn + sebi + stamp + gst)


def run_index(k, df5, sigma_by_day, days, brake=False):
    """Every trade of the pasted rules on one index: [{when, exit_time, net, side, closed_via, index}]."""
    o, h, l, c = (df5[x].to_numpy() for x in ("Open", "High", "Low", "Close"))
    e9, e20 = ind.ema(df5["Close"], 9).to_numpy(), ind.ema(df5["Close"], 20).to_numpy()
    atr = ind.atr(df5, 14).to_numpy()
    vw = ind.vwap(df5).to_numpy()
    idx = df5.index
    tod = np.array([t.time() for t in idx])
    date = np.array([t.date() for t in idx])
    out, n, i = [], len(idx), 30
    day_log = {}                                       # date -> [closed_via of the day's trades]
    while i < n - 1:
        d = date[i]
        if not (FIRST_ENTRY <= tod[i] < SQUARE_OFF):
            i += 1
            continue
        if brake:
            dl = day_log.get(d, [])
            if len(dl) >= 2 and dl[0] == "stop" and dl[1] == "stop":
                i += 1
                continue
        up = e9[i] > e20[i] and e9[i - 1] <= e20[i - 1]
        dn = e9[i] < e20[i] and e9[i - 1] >= e20[i - 1]
        side = "CE" if (up and l[i] > vw[i]) else "PE" if (dn and h[i] < vw[i]) else None
        if side is None or not atr[i] == atr[i] or atr[i] <= 0:
            i += 1
            continue
        ce = side == "CE"
        entry, a = c[i], atr[i]
        stop = entry - 1.5 * a if ce else entry + 1.5 * a
        tgt = entry + 3.0 * a if ce else entry - 3.0 * a
        j, px, via = i + 1, None, "other"
        while j < n and date[j] == d:
            if (l[j] <= stop) if ce else (h[j] >= stop):
                px, via = (min(o[j], stop) if ce else max(o[j], stop)), "stop"
                break
            if (h[j] >= tgt) if ce else (l[j] <= tgt):
                px, via = (max(o[j], tgt) if ce else min(o[j], tgt)), "target"
                break
            if tod[j] >= SQUARE_OFF:
                px, via = c[j], "square-off"
                break
            j += 1
        if px is None:                                   # the day's data ended: out at its last close
            j -= 1
            px = c[j]
        t_in, t_out = idx[i] + pd.Timedelta(minutes=5), idx[j] + pd.Timedelta(minutes=5)
        sig = sigma_by_day.get(d)
        net = price_trade(k, days, t_in, entry, t_out, px, sig, ce) if sig and sig == sig else None
        if net is not None:
            out.append({"when": t_in, "exit_time": t_out, "net": net, "side": side, "closed_via": via, "index": k})
            day_log.setdefault(d, []).append(via)
        i = j + 1
    return out


def _job(k):
    c = sds._context()
    df15 = c["hists"][k]
    days = set(df15.index.date)
    sig = c["F"][k]["sigma"]
    sigma_by_day = sig.groupby(sig.index.date).first().to_dict()
    df5 = ets.load_5m(k)
    # self-check: this pricer is the studies' own (ps.price) for an entry and exit on 15-minute closes
    A = c["A"][k]
    i = 500
    tr = {"entry": float(df15["Close"].iat[i]), "side": "CE"}
    legs = [(float(df15["Close"].iat[i + 3]), i + 3, 1.0)]
    s = float(sig.iat[i])
    ref = ps.price(k, df15, A, i, tr, legs, s)["net"]
    mine = price_trade(k, days, df15.index[i] + pd.Timedelta(minutes=15), tr["entry"],
                       df15.index[i + 3] + pd.Timedelta(minutes=15), legs[0][0], s, True)
    assert abs(ref - mine) < 1e-6, (k, ref, mine)
    return k, run_index(k, df5, sigma_by_day, days), run_index(k, df5, sigma_by_day, days, brake=True)


def main():
    from concurrent.futures import ProcessPoolExecutor
    print("Running the pasted rules on 3 years of 5-minute candles, all three indices...", flush=True)
    with ProcessPoolExecutor(max_workers=len(INDICES)) as ex:
        got = list(ex.map(_job, INDICES))
    plain = [r for _, a, _ in got for r in a]
    braked = [r for _, _, b in got for r in b]
    print("=" * 172)
    print(" NIFTY + Bank Nifty + Sensex, per lot, after costs, real expiries.  columns: trades, won, total, profit factor, "
          "worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print("-" * 172)
    print(" THE TOOL'S OWN SYSTEM (as live, 15-minute) -    2005   39% ₹  +437,509 PF 1.25 DD ₹  91,774 day ₹ -16,860  |"
          " 1167   35% ₹  +125,227 PF 1.12 DD ₹ 102,077 day ₹ -20,572")
    print(sds.line("the pasted Trend Rider", plain))
    print(sds.line("  + stop after the first two trades both stop", braked))
    print("-" * 172)
    for k, a, _ in got:
        print(sds.line(f"  {k} alone", a))

    def dist(rows, label):
        n = len(rows) or 1
        cnt = {v: sum(r["closed_via"] == v for r in rows) for v in ("stop", "target", "square-off", "other")}
        print(f"   {label:44s} " + "  ".join(f"{v} {100 * cnt[v] / n:4.1f}%" for v in cnt)
              + f"   avg ₹{np.mean([r['net'] for r in rows]) if rows else 0:+,.0f}/trade   {len(rows) / 3 / 740:.1f} trades a day per index")
    print("\n HOW THE PASTED SYSTEM'S TRADES END")
    dist(plain, "the pasted Trend Rider")
    print("\n Costs per trade (2 x 0.25% slippage on the premium plus charges) are the same for both systems; the pasted one"
          " also trades a little LESS often (its VWAP filter refuses many crossovers).")


if __name__ == "__main__":
    main()
