#!/usr/bin/env python3
"""
high_win_study.py — is there an 80%+ win strategy on Indian index options that also makes money?
================================================================================
The user, 22 Oct 2026: "do same research and find deeper research where you can find more than 80% win strategy".
A win rate on its own is easy to buy - a near target and a far stop - and says nothing (the +10-point scalp won 82% and
lost money). So the bar here is BOTH: 80%+ of trades won AND profit after every charge, in both periods.

THE RESEARCH: option prices carry a volatility risk premium - implied volatility above the volatility that follows
(Carr & Wu, RFS 2009; Bakshi & Kapadia 2003) - so SELLING options wins most of the time and pays for it with rare,
large losses. Indian vendor backtests of condors and straddles report 65-80% won with no costs or tails shown; Zerodha's
own 45-day Nifty straddle test ran from 90% won (2025) to 55% (2020). SEBI: 88% of individual F&O traders lost money in
FY26, mostly in options, which retail mostly buys. Connors' RSI(2) mean reversion: high win rates on US indices, thin
out-of-sample evidence.

PRICING: old option chains are not kept, so premiums are Black-Scholes on India VIX (the day before's close - known
at the open) times the smile MEASURED on the server's recorded chains (10 Sep - 22 Oct 2026, 18,311 option prices,
four indices; smile_calib on the server): implied volatility / VIX by distance from the price in expected moves
(m = ln(K/S) / (VIX * sqrt(T))), on ordinary days and on expiry day, where every strike trades at about 2.0-2.3 x VIX.
Every result is also re-run with 15% less premium (CHEAP), in case the 27 days flatter it. Charges: Rs 20 an order,
STT on option sales (and 0.125% of intrinsic on a long option settled in the money), exchange and SEBI fees, stamp on
buys, 18% GST; slippage 1% of each premium or Rs 0.25, whichever is more, each way. Settlement at the expiry day's last
15-minute close. Per lot at today's lot sizes.

FIXED BEFORE ANY RESULT WAS SEEN (22 Oct 2026). Nifty, Bank Nifty, Sensex and Midcap Select, 3 years:
  IC_W  weekly iron condor, held to expiry: entered at 09:30 on the trading day D before expiry (D = 1 and 3), short
        put and call k expected moves away to expiry (k = 1.0 and 1.5; strikes rounded outward), long wings 1% of the
        index further out (defined risk)
  IC_0  expiry-day iron condor: entered 09:30 on expiry day, the same k, wings 1% further; a side whose short strike
        the index crosses (a 15-minute high/low) is bought back at that candle's close, the rest at 15:15
  RSI2  Connors: on the daily close, long when RSI(2) < 10 above the 200-day average, short when RSI(2) > 90 below it;
        out at the close back across the 5-day average; one lot of the future, Rs 1 a point slippage each way, charges
  A variant PASSES only if 80%+ of its trades won AND it made money - in the first two years AND the held-out year (from
  15 Aug 2025), pooled over the indices - and still made money in both with CHEAP premiums. For each: trades, won, total,
  average win, average loss, the worst trade, the worst week, profit factor, worst drop, and the most it could lose.

ROUND 2, FIXED AFTER ROUND 1 AND BEFORE ITS OWN RESULT (22 Oct 2026; high_win_study_round2_results.txt): round 1's
1.5-expected-move condors won 85-92% (weekly) but failed the 15%-less-premium check, and the expiry-day ones made
money robustly but won only 62-74%. So the same variants at k = 2.0 (IC_W with D = 1 and 3; IC_0), the same pass rule,
plus an information line for IC_0 with expiry day priced on the ordinary-day smile (about half the premium).

ROUND 3, AFTER A REAL-PRICE CHECK (22 Oct 2026; high_win_study_round3_results.txt): round 2's weekly condor at 2.0
expected moves "passed" - but real 15-minute prices of the far strikes on the live expiries (far_otm_check: 4,455 prices,
8 trading days) are only 0.3-0.6 x the model on Nifty, Sensex and Bank Nifty at 2-3 expected moves (Midcap ~1.1-1.2 x):
the smile measured within ~1-3% of the price over-prices the far strikes. Round 3 corrects ordinary-day far strikes by
those measured ratios (REAL_CAL, by index, side and distance), buys long wings at no less than Rs 0.50, and re-runs
every condor (k 1.0 / 1.5 / 2.0; weekly D 1 / 3; expiry day). Same pass rule.

RESULT (22 Oct 2026) - NO 80%+ WIN STRATEGY THAT MAKES MONEY ONCE REAL PRICES ARE USED. NOTHING BUILT.
  Round 1 (high_win_study_results.txt): no variant passed. Weekly condors at 1.0 lost heavily (69-75% won); at 1.5 won
  85-92% but failed the 15%-less-premium check; expiry-day condors made money robustly but won only 53-74%; RSI(2) won
  61% and lost money held-out.
  Round 2 (..._round2_results.txt): the weekly condor at 2.0 expected moves, entered 3 days before expiry, PASSED - won
  95% / 99%, +147,583 / +131,369 per lot.
  The real-price check (far_otm_check.py) undid it: far strikes trade at 0.3-0.6 x the model on Nifty, Sensex and Bank
  Nifty. Round 3 (..._round3_results.txt), priced with those ratios: the same condor still wins 95% / 99% but earns
  only ~Rs 330-380 a win against ~Rs 7,400 a loss - -13,953 in the first two years; no weekly condor passes. The
  expiry-day condors (unchanged - their strikes lie inside the recorded range) make money in both periods but win 53-78%.
  And on the real recorded premiums of the 11 expiry days since 10 Sep (ic0_real_check.py) the expiry-day condor won
  18% / 36% and lost Rs 19,950 / 13,321 - the model, same days, was about Rs 1,000-2,000 a trade too kind, more than the
  strategy's three-year average profit. A high win rate here is bought with thin credits and rare large losses; with
  real prices and costs the arithmetic did not come out positive in anything tested.

    /usr/bin/python3 high_win_study.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
# IV / VIX by m (expected-move units), measured 22 Oct 2026 on the recorded chains (OTM side; median over 4 indices)
SMILE = {
    (False, "CE"): {0: 1.18, 0.5: 1.08, 1.0: 1.01, 1.5: 1.25, 2.0: 1.39, 2.5: 1.46, 3.0: 1.59, 3.5: 1.77},
    (False, "PE"): {0: 0.90, -0.5: 0.97, -1.0: 1.04, -1.5: 1.30, -2.0: 1.47, -2.5: 1.56, -3.0: 1.67, -3.5: 1.67},
    (True, "CE"): {0: 2.09, 0.5: 2.03, 1.0: 2.03, 1.5: 2.12, 2.0: 2.20, 2.5: 2.23, 3.0: 2.26, 3.5: 2.76},
    (True, "PE"): {0: 2.16, -0.5: 2.13, -1.0: 2.11, -1.5: 2.30, -2.0: 2.21, -2.5: 2.34, -3.0: 2.42, -3.5: 2.31},
}
SLIP_PCT, SLIP_MIN = 0.01, 0.25
IST = "Asia/Kolkata"
FLAT = False          # round 2's information line: expiry day priced on the ordinary-day smile (about half the premium)
CAL = False           # round 3: far strikes corrected by real prices (REAL_CAL), long wings bought at no less than Rs 0.50
# real / model price of out-of-the-money options on ordinary days, by distance in expected moves - 4,455 real 15-minute
# prices of the live expiries' far strikes, 8 trading days to 22 Oct 2026 (far_otm_check on the server); 0.5 anchors 1.0
REAL_CAL = {
    ("BANKNIFTY", "CE"): {0.5: 1.0, 1.0: 0.97, 1.5: 0.58, 2.0: 0.42, 2.5: 0.44, 3.0: 0.48, 3.5: 0.41},
    ("BANKNIFTY", "PE"): {0.5: 1.0, 1.0: 0.94, 1.5: 0.72, 2.0: 0.59, 2.5: 0.61, 3.0: 0.66, 3.5: 0.91},
    ("MIDCPNIFTY", "CE"): {0.5: 1.0, 1.0: 1.65, 1.5: 1.23, 2.0: 1.02, 2.5: 1.27, 3.0: 1.23, 3.5: 1.06},
    ("MIDCPNIFTY", "PE"): {0.5: 1.0, 1.0: 1.94, 1.5: 1.47, 2.0: 1.21, 2.5: 1.16, 3.0: 1.14, 3.5: 1.64, 4.0: 2.47},
    ("NIFTY", "CE"): {0.5: 1.0, 1.0: 0.73, 1.5: 0.39, 2.0: 0.28, 2.5: 0.31, 3.0: 0.41},
    ("NIFTY", "PE"): {0.5: 1.0, 1.0: 0.93, 1.5: 0.62, 2.0: 0.48, 2.5: 0.47, 3.0: 0.47, 3.5: 0.75, 4.0: 1.13},
    ("SENSEX", "CE"): {0.5: 1.0, 1.0: 0.80, 1.5: 0.52, 2.0: 0.34, 2.5: 0.31, 3.0: 0.37, 3.5: 0.42, 4.0: 0.73},
    ("SENSEX", "PE"): {0.5: 1.0, 1.0: 0.96, 1.5: 0.63, 2.0: 0.48, 2.5: 0.50, 3.0: 0.53, 3.5: 0.85, 4.0: 1.76},
}
WING_FLOOR = 0.50


def ratio(expiry_day, opt, m):
    t = SMILE[(expiry_day, opt)]
    ks = sorted(t)
    m = min(max(m, ks[0]), ks[-1])
    return float(np.interp(m, ks, [t[k] for k in ks]))


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S, K, T, v, call):
    if T <= 1e-9 or v <= 0:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (math.log(S / K) + 0.5 * v * v * T) / (v * math.sqrt(T))
    d2 = d1 - v * math.sqrt(T)
    return S * ncdf(d1) - K * ncdf(d2) if call else K * ncdf(-d2) - S * ncdf(-d1)


def price(S, K, T, vix, opt, expiry_day, cheap, key=None):
    """An option's model premium: Black-Scholes at VIX x the measured smile at its distance (round 3: times the real /
    model ratio measured on the far strikes, on ordinary days)."""
    v0 = vix / 100
    m = math.log(K / S) / (v0 * math.sqrt(max(T, 1e-6)))
    p = bs(S, K, T, v0 * ratio(expiry_day and not FLAT, opt, m), opt == "CE")
    if CAL and key and not expiry_day and (key, opt) in REAL_CAL:
        t = REAL_CAL[(key, opt)]
        ks = sorted(t)
        am = min(max(abs(m), ks[0]), ks[-1])
        p *= float(np.interp(am, ks, [t[x] for x in ks])) if abs(m) >= ks[0] else 1.0
    return p * (0.85 if cheap else 1.0)


def slip(p):
    return max(p * SLIP_PCT, SLIP_MIN)


def charges(key, sells, buys, orders, itm_long_intrinsic=0.0):
    import regime_study as rs
    exch = "BSE" if key == "SENSEX" else "NSE"
    txn = rs.TXN.get(exch, rs.TXN.get("NSE")) * (sells + buys)
    brokerage = rs.BROKERAGE_PER_ORDER * orders
    return (brokerage + rs.STT_SELL * sells + 0.00125 * itm_long_intrinsic + txn + rs.SEBI_PER_RUPEE * (sells + buys)
            + rs.STAMP_BUY * buys + rs.GST * (brokerage + txn + rs.SEBI_PER_RUPEE * (sells + buys)))


def years_to(exp, t):
    e = pd.Timestamp(f"{exp} 15:30", tz=IST)
    return max((e - t).total_seconds() / (365 * 86400), 1e-7)


def setup(key):
    import backtest_intraday as bt
    import index_candidates_study as ics
    import pro_study as ps
    import regime_study as rs
    df = bt.fetch_history(key, years=3, use_cache=True)
    days = sorted(set(df.index.date))
    tset = set(days)
    real = ps._scheduled
    if key == "MIDCPNIFTY":
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    exp_of = {d: ps.expiry_on_or_after(key, d, tset) for d in days}
    ps._scheduled = real
    import bisect
    vix = rs.load_vix().sort_index()                 # indexed by date: the day's closing VIX
    vd, vv = list(vix.index), [float(x) for x in vix.values]
    vix_prev = {}
    for d in days:
        n = bisect.bisect_left(vd, d) - 1            # the last close before d - known at d's open
        if n >= 0 and vv[n] == vv[n]:
            vix_prev[d] = vv[n]
    return df, days, exp_of, vix_prev


def condor(key, df, day, t_in, S, vix, exp, k, cheap, intraday):
    """One iron condor: (net per lot, max loss per lot) or None."""
    step = config.INSTRUMENTS[key]["strike_step"]
    lot = config.INSTRUMENTS[key]["lot_size"]
    T = years_to(exp, t_in)
    em = vix / 100 * math.sqrt(T)
    sp = math.floor(S * math.exp(-k * em) / step) * step
    sc = math.ceil(S * math.exp(k * em) / step) * step
    w = max(step, round(0.01 * S / step) * step)
    lp, lc = sp - w, sc + w
    ed = intraday
    legs = {"sp": (sp, "PE", -1), "sc": (sc, "CE", -1), "lp": (lp, "PE", +1), "lc": (lc, "CE", +1)}
    px_in = {n: price(S, K, T, vix, o, ed, cheap, key) for n, (K, o, _) in legs.items()}
    if CAL:
        for n in ("lp", "lc"):
            px_in[n] = max(px_in[n], WING_FLOOR)
    if px_in["sp"] <= 0.05 or px_in["sc"] <= 0.05:
        return None
    sells_in = px_in["sp"] - slip(px_in["sp"]) + px_in["sc"] - slip(px_in["sc"])
    buys_in = px_in["lp"] + slip(px_in["lp"]) + px_in["lc"] + slip(px_in["lc"])
    credit = sells_in - buys_in
    day_bars = df[df.index.date == day]
    after = day_bars[day_bars.index > t_in] if intraday else None
    sells_out = buys_out = 0.0
    orders = 4
    itm_long = 0.0
    if intraday:
        open_sides = {"put": True, "call": True}
        pay = 0.0
        last_t = None
        for t, bar in (after.iterrows() if after is not None else []):
            close_t = t + pd.Timedelta(minutes=15)
            last_t = close_t
            if close_t.time() > pd.Timestamp("15:15").time():
                break
            Tn = years_to(exp, close_t)
            c = float(bar["Close"])
            for side, (sn, ln, o) in (("put", ("sp", "lp", "PE")), ("call", ("sc", "lc", "CE"))):
                if not open_sides[side]:
                    continue
                breached = float(bar["Low"]) <= legs[sn][0] if side == "put" else float(bar["High"]) >= legs[sn][0]
                if breached:
                    s_px = price(c, legs[sn][0], Tn, vix, o, True, cheap, key)
                    l_px = price(c, legs[ln][0], Tn, vix, o, True, cheap, key)
                    buys_out += s_px + slip(s_px)
                    sells_out += max(l_px - slip(l_px), 0)
                    pay += (s_px + slip(s_px)) - max(l_px - slip(l_px), 0)
                    orders += 2
                    open_sides[side] = False
        # whatever is still open goes at 15:15
        t_out = pd.Timestamp(f"{day} 15:15", tz=IST)
        bars = day_bars[day_bars.index + pd.Timedelta(minutes=15) <= t_out]
        c_out = float(bars["Close"].iloc[-1]) if len(bars) else S
        Tn = years_to(exp, t_out)
        for side, (sn, ln, o) in (("put", ("sp", "lp", "PE")), ("call", ("sc", "lc", "CE"))):
            if open_sides[side]:
                s_px = price(c_out, legs[sn][0], Tn, vix, o, True, cheap, key)
                l_px = price(c_out, legs[ln][0], Tn, vix, o, True, cheap, key)
                if s_px > 0.05:
                    buys_out += s_px + slip(s_px)
                    orders += 1
                if l_px > 0.05:
                    sells_out += max(l_px - slip(l_px), 0)
                    orders += 1
                pay += ((s_px + slip(s_px)) if s_px > 0.05 else 0) - (max(l_px - slip(l_px), 0) if l_px > 0.05 else 0)
        gross = credit - pay
    else:
        eb = df[df.index.date == exp]
        if eb.empty:
            return None
        settle = float(eb["Close"].iloc[-1])
        intr = {n: max(0.0, (settle - K) if o == "CE" else (K - settle)) for n, (K, o, _) in legs.items()}
        pay = intr["sp"] + intr["sc"] - intr["lp"] - intr["lc"]
        itm_long = intr["lp"] + intr["lc"]
        gross = credit - pay
    net = gross * lot - charges(key, (sells_in + sells_out) * lot, (buys_in + buys_out) * lot, orders, itm_long * lot)
    max_loss = (w - credit) * lot
    return net, max_loss


def strategy_rows(key, variant, cheap):
    df, days, exp_of, vix_prev = setup(key)
    rows = []
    pos = {d: n for n, d in enumerate(days)}
    kind, k, D = variant
    for d in days:
        exp = exp_of.get(d)
        if exp is None or d not in vix_prev:
            continue
        if kind == "IC_W":
            if exp not in pos or pos[exp] - pos[d] != D:
                continue
        elif kind == "IC_0":
            if exp != d:
                continue
        t_in = pd.Timestamp(f"{d} 09:30", tz=IST)
        b = df[(df.index.date == d)]
        first = b[b.index + pd.Timedelta(minutes=15) <= t_in]
        if first.empty:
            continue
        S = float(first["Close"].iloc[-1])
        r = condor(key, df, d, t_in, S, vix_prev[d], exp, k, cheap, intraday=(kind == "IC_0"))
        if r is None:
            continue
        rows.append({"when": t_in, "net": r[0], "max_loss": r[1], "index": key})
    return rows


def rsi2_rows(key):
    import backtest_intraday as bt
    import regime_study as rs
    df = bt.fetch_history(key, years=3, use_cache=True)
    c = df["Close"].groupby(df.index.date).last()
    idx = pd.to_datetime(c.index).tz_localize(IST) + pd.Timedelta(hours=15, minutes=30)
    c.index = idx
    d = c.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    rsi = 100 - 100 / (1 + up.ewm(alpha=1 / 2, adjust=False).mean() / dn.ewm(alpha=1 / 2, adjust=False).mean())
    sma200, sma5 = c.rolling(200).mean(), c.rolling(5).mean()
    lot = config.INSTRUMENTS[key]["lot_size"]
    rows, pos = [], None
    for i in range(201, len(c)):
        px = float(c.iloc[i])
        if pos is None:
            if px > sma200.iloc[i] and rsi.iloc[i] < 10:
                pos = (1, px, c.index[i])
            elif px < sma200.iloc[i] and rsi.iloc[i] > 90:
                pos = (-1, px, c.index[i])
        else:
            s, e, t0 = pos
            if (s > 0 and px > sma5.iloc[i]) or (s < 0 and px < sma5.iloc[i]):
                pts = s * (px - e) - 2.0
                turnover = (e + px) * lot
                cost = 2 * 20 * 1.18 + 0.0002 * px * lot + 0.0000173 * turnover * 1.18 + 0.00002 * e * lot
                rows.append({"when": t0, "net": pts * lot - cost, "max_loss": None, "index": key})
                pos = None
    return rows


def _job(job):
    global FLAT, CAL
    key, variant, cheap, *rest = job
    CAL = bool(rest and rest[0])
    FLAT = cheap == "flat"
    cheap = cheap is True
    if variant[0] == "RSI2":
        return job, rsi2_rows(key)
    return job, strategy_rows(key, variant, cheap)


def summarize(rows):
    import regime_study as rs
    out = {}
    for p, sel in (("is", [r for r in rows if r["when"] < rs.SPLIT]), ("oos", [r for r in rows if r["when"] >= rs.SPLIT])):
        net = np.array([r["net"] for r in sel], float)
        if not len(net):
            out[p] = None
            continue
        eq = np.cumsum(net)
        dd = float(np.max(np.maximum.accumulate(np.r_[0, eq])[1:] - eq)) if len(eq) else 0
        wk = pd.Series(net, index=[pd.Timestamp(r["when"]).to_period("W") for r in sel]).groupby(level=0).sum()
        wins, losses = net[net > 0], net[net <= 0]
        ml = [r["max_loss"] for r in sel if r.get("max_loss") is not None]
        out[p] = {"n": len(net), "won": len(wins) / len(net), "total": float(net.sum()),
                  "avg_win": float(wins.mean()) if len(wins) else 0, "avg_loss": float(losses.mean()) if len(losses) else 0,
                  "worst": float(net.min()), "worst_week": float(wk.min()), "pf": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("inf"),
                  "dd": dd, "max_loss": float(np.median(ml)) if ml else None}
    return out


def main(round2=False, round3=False):
    import multiprocessing as mp
    variants = ([("IC_W", k_, d_) for k_ in (1.0, 1.5, 2.0) for d_ in (1, 3)] + [("IC_0", k_, 0) for k_ in (1.0, 1.5, 2.0)]
                if round3 else
                [("IC_W", 2.0, 1), ("IC_W", 2.0, 3), ("IC_0", 2.0, 0)] if round2 else
                [("IC_W", 1.0, 1), ("IC_W", 1.0, 3), ("IC_W", 1.5, 1), ("IC_W", 1.5, 3), ("IC_0", 1.0, 0), ("IC_0", 1.5, 0)])
    jobs = [(k, v, cheap, round3) for v in variants for k in INDICES for cheap in (False, True)]
    if round2:
        jobs += [(k, v, "flat") for v in variants if v[0] == "IC_0" for k in INDICES]
    else:
        jobs += [(k, ("RSI2", 0, 0), False) for k in INDICES]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = {j[:3]: rows for j, rows in pool.map(_job, jobs, chunksize=1)}
    W = 170
    print("=" * W)
    print(" 80%+ WIN STRATEGIES THAT MAKE MONEY? Per lot after charges and slippage, ₹, pooled over Nifty, Bank Nifty, Sensex,"
          " Midcap Select. Columns: trades, won, total, avg win, avg loss, worst trade, worst week, PF, worst drop")
    print("=" * W)
    f = lambda s: ("no trades" if s is None else
                   f"{s['n']:>4} won {s['won']:4.0%} ₹{s['total']:>+9,.0f} win ₹{s['avg_win']:>+6,.0f} loss ₹{s['avg_loss']:>+7,.0f}"
                   f" worst ₹{s['worst']:>+8,.0f} wk ₹{s['worst_week']:>+8,.0f} PF {s['pf']:4.2f} DD ₹{s['dd']:>7,.0f}")
    names = {"IC_W": "weekly condor", "IC_0": "expiry-day condor", "RSI2": "RSI(2) mean reversion"}
    passed = []
    for v in variants + ([] if (round2 or round3) else [("RSI2", 0, 0)]):
        title = (f"{names[v[0]]}, short strikes {v[1]} expected moves" + (f", in {v[2]} day(s) before expiry" if v[0] == "IC_W" else "")
                 if v[0] != "RSI2" else names["RSI2"] + " (futures)")
        print(f"\n {title}")
        res = {}
        for cheap in ((False, True) if v[0] != "RSI2" else (False,)):
            rows = sorted([r for k in INDICES for r in got[(k, v, cheap)]], key=lambda r: r["when"])
            s = summarize(rows)
            res[cheap] = s
            lab = "measured premiums" if not cheap else "15% less premium"
            print(f"   {lab:18s} first two years {f(s['is'])}")
            print(f"   {'':18s} held-out        {f(s['oos'])}")
            ml = s["is"]["max_loss"] if s["is"] else None
            if ml and not cheap:
                print(f"   {'':18s} the most one condor can lose (median, per lot): ₹{ml:,.0f}")
        if (INDICES[0], v, "flat") in got:
            s = summarize(sorted([r for k in INDICES for r in got[(k, v, "flat")]], key=lambda r: r["when"]))
            print(f"   {'(info) expiry day':18s} first two years {f(s['is'])}")
            print(f"   {'priced like any day':18s} held-out        {f(s['oos'])}")
        for key in INDICES:
            rows = got[(key, v, False)]
            s = summarize(rows)
            print(f"      {key:11s} " + "   ".join(f"{p}: " + ("—" if s[p] is None else f"{s[p]['n']} won {s[p]['won']:.0%} ₹{s[p]['total']:+,.0f}")
                                                 for p in ("is", "oos")))
        ok = all(res[False][p] and res[False][p]["won"] >= 0.80 and res[False][p]["total"] > 0 for p in ("is", "oos"))
        if v[0] != "RSI2":
            ok = ok and all(res[True][p] and res[True][p]["total"] > 0 for p in ("is", "oos"))
        print(f"   {'PASSES' if ok else 'does not pass'} (80%+ won and profit in both periods" + ("" if v[0] == "RSI2" else ", profit with 15% less premium too") + ")")
        if ok:
            passed.append(title)
    print("\n VERDICT: " + ("PASSED - " + "; ".join(passed) if passed else "NONE PASSED"))


if __name__ == "__main__":
    main(round2="round2" in sys.argv[1:], round3="round3" in sys.argv[1:])
